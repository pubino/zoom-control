"""User-level Zoom OAuth for a single host account (e.g. orfetalks@princeton.edu).

There is deliberately no account-wide credential anywhere. Each Zoom host authorizes the
user-managed app once, on the node, and the resulting tokens can act only as that user.

Zoom refresh tokens are single use: every refresh returns a new refresh token and
invalidates the old one, and an unused refresh token expires after 90 days. So each
host has exactly one token record, and every read-refresh-write happens under a lock.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from .errors import ZoomAuthError, ZoomError

AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"
KEYCHAIN_SERVICE = "zoom-control"


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class TokenRecord:
    host: str  # Zoom user email the tokens belong to (verified via /users/me at login)
    client_id: str
    refresh_token: str
    access_token: str = ""
    expires_at: float = 0.0  # epoch seconds for access_token
    refreshed_at: float = 0.0  # when refresh_token was last rotated
    client_secret: str | None = None  # None for a PKCE public client
    scope: str = ""
    zoom_user_id: str = ""

    def access_valid(self, now: float, skew: float = 60.0) -> bool:
        return bool(self.access_token) and now < self.expires_at - skew

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> TokenRecord:
        data = json.loads(raw)
        known = {f for f in cls.__dataclass_fields__}  # tolerate fields added by newer versions
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary(self, now: float) -> str:
        access = f"access token valid until {_iso(self.expires_at)}" if self.access_valid(now) else "access token expired"
        age_days = (now - self.refreshed_at) / 86400 if self.refreshed_at else float("nan")
        return f"{self.host}: {access}; refresh token rotated {age_days:.0f} day(s) ago; scopes: {self.scope or '?'}"


def normalize_host(host: str) -> str:
    return host.strip().lower()


# ----------------------------------------------------------------------- stores


class TokenStore(Protocol):
    def load(self, host: str) -> TokenRecord | None: ...
    def save(self, record: TokenRecord) -> None: ...
    def delete(self, host: str) -> None: ...
    def hosts(self) -> list[str]: ...
    def lock(self, host: str) -> contextlib.AbstractContextManager[None]: ...
    def describe(self) -> str: ...


class _LockDir:
    def __init__(self, root: Path):
        self.root = root

    @contextlib.contextmanager
    def lock(self, host: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / f"{normalize_host(host)}.lock"
        with path.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)  # blocks: refreshes are short
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


class FileTokenStore:
    """JSON files (0600) in a 0700 directory. Default off macOS, and used in tests."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._locks = _LockDir(self.root / "locks")

    def _path(self, host: str) -> Path:
        return self.root / f"{normalize_host(host)}.json"

    def load(self, host: str) -> TokenRecord | None:
        p = self._path(host)
        return TokenRecord.from_json(p.read_text()) if p.exists() else None

    def save(self, record: TokenRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        p = self._path(record.host)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(record.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)  # atomic: a crash never leaves a half-written refresh token

    def delete(self, host: str) -> None:
        self._path(host).unlink(missing_ok=True)

    def hosts(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json")) if self.root.exists() else []

    def lock(self, host: str):
        return self._locks.lock(host)

    def describe(self) -> str:
        return f"file:{self.root}"


class KeychainTokenStore:
    """macOS login keychain of the node user (av-runner), via /usr/bin/security.

    Items are created with `-T /usr/bin/security` so later reads/updates from
    unattended jobs don't raise a keychain ACL prompt. The login keychain is
    unlocked by automatic login.
    """

    def __init__(self, service: str = KEYCHAIN_SERVICE, state_dir: Path | None = None,
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.service = service
        self.state_dir = state_dir or Path.home() / "Library" / "Application Support" / "zoom-control"
        self._locks = _LockDir(self.state_dir / "locks")
        self._run = run

    def _security(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = self._run(["/usr/bin/security", *args], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise ZoomAuthError(f"keychain: security {args[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc

    def load(self, host: str) -> TokenRecord | None:
        proc = self._security("find-generic-password", "-s", self.service, "-a", normalize_host(host), "-w",
                              check=False)
        if proc.returncode == 44:  # errSecItemNotFound
            return None
        if proc.returncode != 0:
            raise ZoomAuthError(f"keychain read failed ({proc.returncode}): {proc.stderr.strip()} "
                                "— is the login keychain unlocked (GUI session)?")
        return TokenRecord.from_json(proc.stdout.strip())

    def save(self, record: TokenRecord) -> None:
        self._security("add-generic-password", "-U", "-s", self.service, "-a", normalize_host(record.host),
                       "-l", f"zoom-control {record.host}", "-T", "/usr/bin/security", "-w", record.to_json())
        index = self._index()
        if normalize_host(record.host) not in index:
            self._write_index(sorted({*index, normalize_host(record.host)}))

    def delete(self, host: str) -> None:
        self._security("delete-generic-password", "-s", self.service, "-a", normalize_host(host), check=False)
        self._write_index([h for h in self._index() if h != normalize_host(host)])

    def _index(self) -> list[str]:
        p = self.state_dir / "hosts.json"
        return json.loads(p.read_text()) if p.exists() else []

    def _write_index(self, hosts: list[str]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.state_dir / "hosts.json").write_text(json.dumps(hosts))

    def hosts(self) -> list[str]:
        return self._index()

    def lock(self, host: str):
        return self._locks.lock(host)

    def describe(self) -> str:
        return f"keychain:{self.service}"


def default_store(env: dict[str, str] | None = None) -> TokenStore:
    """ZOOMCTL_TOKEN_STORE = keychain | file:/path. Default: keychain on macOS, file elsewhere."""
    env = dict(os.environ if env is None else env)
    spec = env.get("ZOOMCTL_TOKEN_STORE", "")
    if spec.startswith("file:"):
        return FileTokenStore(Path(spec[5:]).expanduser())
    if spec == "keychain" or (not spec and sys.platform == "darwin"):
        return KeychainTokenStore()
    if spec:
        raise ZoomAuthError(f"unknown ZOOMCTL_TOKEN_STORE {spec!r} (use 'keychain' or 'file:/path')")
    base = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return FileTokenStore(base / "zoomctl" / "tokens")


# ------------------------------------------------------------------ OAuth flows


@dataclass
class OAuthApp:
    client_id: str
    client_secret: str | None
    redirect_uri: str
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20.0))
    token_url: str = TOKEN_URL
    authorize_url: str = AUTHORIZE_URL

    def _auth(self) -> tuple[str, str] | None:
        return (self.client_id, self.client_secret) if self.client_secret else None

    def begin(self) -> tuple[str, str, str]:
        """Returns (url_to_open, state, pkce_verifier)."""
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        query = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
                 "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{self.authorize_url}?{urlencode(query)}", state, verifier

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        if not self.client_secret:
            data = {**data, "client_id": self.client_id}
        try:
            resp = self.http.post(self.token_url, data=data, auth=self._auth())
        except httpx.TransportError as exc:
            raise ZoomAuthError(f"token endpoint unreachable: {exc}") from exc
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text[:300]
        if resp.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            reason = body.get("reason") or body.get("error") if isinstance(body, dict) else body
            raise ZoomAuthError(f"token request ({data['grant_type']}) failed: HTTP {resp.status_code}: {reason}",
                                resp.status_code, body)
        return body

    def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        return self._token_request({"grant_type": "authorization_code", "code": code,
                                    "redirect_uri": self.redirect_uri, "code_verifier": verifier})

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def revoke(self, token: str) -> None:
        resp = self.http.post(REVOKE_URL, data={"token": token}, auth=self._auth())
        if resp.status_code >= 400:
            raise ZoomAuthError(f"revoke failed: HTTP {resp.status_code}", resp.status_code)


def apply_token_response(record: TokenRecord, body: dict[str, Any], now: float) -> TokenRecord:
    record.access_token = body["access_token"]
    record.expires_at = now + float(body.get("expires_in", 3600))
    if body.get("refresh_token"):
        record.refresh_token = body["refresh_token"]
        record.refreshed_at = now
    record.scope = body.get("scope", record.scope)
    return record


class UserTokenProvider:
    """Supplies access tokens for exactly one Zoom host, refreshing under a cross-process lock."""

    def __init__(self, host: str, store: TokenStore, *, http: httpx.Client | None = None,
                 token_url: str = TOKEN_URL, clock: Callable[[], float] = _now):
        self.host = normalize_host(host)
        self.store = store
        self._http = http
        self._token_url = token_url
        self._clock = clock
        self._cached: TokenRecord | None = None

    def _record(self) -> TokenRecord:
        rec = self.store.load(self.host)
        if rec is None:
            raise ZoomAuthError(f"no Zoom authorization for {self.host} in {self.store.describe()} — "
                                f"run `zoomctl auth login --host {self.host}` on this node")
        if normalize_host(rec.host) != self.host:
            raise ZoomAuthError(f"token record for {self.host} belongs to {rec.host}; refusing to use it")
        return rec

    def access_token(self, rejected: str | None = None) -> str:
        """Return a valid token. Pass `rejected` after a 401 to force a refresh unless
        another process already replaced that token."""
        now = self._clock()
        if self._cached and self._cached.access_valid(now) and self._cached.access_token != rejected:
            return self._cached.access_token
        with self.store.lock(self.host):
            rec = self._record()
            if rec.access_valid(now) and rec.access_token != rejected:
                self._cached = rec
                return rec.access_token
            app = OAuthApp(rec.client_id, rec.client_secret, redirect_uri="",
                           http=self._http or httpx.Client(timeout=20.0), token_url=self._token_url)
            try:
                body = app.refresh(rec.refresh_token)
            except ZoomAuthError as exc:
                if exc.status in (400, 401):
                    raise ZoomAuthError(
                        f"Zoom rejected the refresh token for {self.host} ({exc}). It expires after 90 days "
                        f"unused, or was revoked. Re-authorize on the node: zoomctl auth login --host {self.host}",
                        exc.status, exc.body) from exc
                raise
            # Save before anything else can fail: the old refresh token is now dead.
            self.store.save(apply_token_response(rec, body, now))
            self._cached = rec
            return rec.access_token


__all__ = ["FileTokenStore", "KeychainTokenStore", "OAuthApp", "TokenRecord", "TokenStore", "UserTokenProvider",
           "ZoomError", "apply_token_response", "default_store", "normalize_host"]
