"""One-time user authorization on the node: `zoomctl auth login --host orfetalks@princeton.edu`."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .zoom.client import DEFAULT_API
from .zoom.errors import ZoomAuthError
from .zoom.tokens import OAuthApp, TokenRecord, TokenStore, apply_token_response, normalize_host

DEFAULT_REDIRECT = "http://127.0.0.1:8765/zoom/callback"


def parse_callback(url: str, expected_state: str) -> str:
    """Extract the authorization code from the redirect URL, validating `state`."""
    q = parse_qs(urlparse(url.strip()).query)
    if "error" in q:
        raise ZoomAuthError(f"authorization denied: {q['error'][0]} {q.get('error_description', [''])[0]}".strip())
    state = q.get("state", [""])[0]
    if state != expected_state:
        raise ZoomAuthError("state mismatch — paste the URL from THIS login attempt")
    code = q.get("code", [""])[0]
    if not code:
        raise ZoomAuthError("redirect URL has no ?code= parameter")
    return code


def is_loopback(redirect_uri: str) -> bool:
    u = urlparse(redirect_uri)
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "::1") and u.port is not None


def wait_for_loopback(redirect_uri: str, timeout: float = 300.0) -> str:
    """Serve one request on the loopback redirect and return the full callback URL."""
    u = urlparse(redirect_uri)
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if urlparse(self.path).path != u.path:
                self.send_response(404)
                self.end_headers()
                return
            captured["url"] = f"{u.scheme}://{u.netloc}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<p>zoom-control: authorization received. You can close this tab.</p>")

        def log_message(self, *args):
            pass

    server = HTTPServer((u.hostname, u.port), Handler)
    server.timeout = 1.0
    deadline = time.monotonic() + timeout
    try:
        while "url" not in captured and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "url" not in captured:
        raise ZoomAuthError(f"no redirect received on {redirect_uri} within {int(timeout)}s")
    return captured["url"]


def complete_login(app: OAuthApp, host: str, code: str, verifier: str, store: TokenStore, *,
                   api_base: str = DEFAULT_API, clock: Callable[[], float] = time.time) -> tuple[TokenRecord, dict]:
    """Exchange the code, prove the token belongs to `host`, then (and only then) store it."""
    host = normalize_host(host)
    body = app.exchange_code(code, verifier)
    me = app.http.get(f"{api_base.rstrip('/')}/users/me",
                      headers={"Authorization": f"Bearer {body['access_token']}"})
    if me.status_code != 200:
        raise ZoomAuthError(f"GET /users/me failed: HTTP {me.status_code} — add the user:read:user scope",
                            me.status_code)
    user: dict[str, Any] = me.json()
    email = normalize_host(user.get("email", ""))
    if email != host:
        try:
            app.revoke(body["access_token"])
        except ZoomAuthError:
            pass
        raise ZoomAuthError(f"you signed in as {email or '?'}, but --host is {host}. Sign out of Zoom in the "
                            "browser (or use a private window) and sign in as the host account.")
    rec = TokenRecord(host=host, client_id=app.client_id, client_secret=app.client_secret,
                      refresh_token=body.get("refresh_token", ""), zoom_user_id=user.get("id", ""))
    apply_token_response(rec, body, clock())
    if not rec.refresh_token:
        raise ZoomAuthError("token response had no refresh_token; is this a user-managed General app?")
    with store.lock(host):
        store.save(rec)
    return rec, user


def logout(host: str, store: TokenStore, http: httpx.Client | None = None) -> bool:
    host = normalize_host(host)
    with store.lock(host):
        rec = store.load(host)
        if rec is None:
            return False
        app = OAuthApp(rec.client_id, rec.client_secret, "", http=http or httpx.Client(timeout=20.0))
        try:
            app.revoke(rec.refresh_token)
        finally:
            store.delete(host)
    return True


def run_in_thread(fn: Callable[[], str]) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def target():
        try:
            out["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            out["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, out
