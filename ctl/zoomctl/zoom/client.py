"""Zoom REST client acting as ONE user via user-level OAuth, with retry/backoff on 429/5xx.

Every call goes to that user's own resources (`/users/me/...`, or webinars it hosts), so
the client cannot reach any other account member even if misconfigured.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx

from .errors import ZoomAuthError, ZoomError

DEFAULT_API = "https://api.zoom.us/v2"


class TokenProvider(Protocol):
    host: str

    def access_token(self, rejected: str | None = None) -> str: ...


class ZoomClient:
    def __init__(
        self,
        tokens: TokenProvider,
        *,
        api_base: str | None = None,
        http: httpx.Client | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._tokens = tokens
        self.host = tokens.host
        self._api = (api_base or os.environ.get("ZOOM_API_BASE") or DEFAULT_API).rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(20.0))
        self._max_retries = max_retries
        self._sleep = sleep
        self._verified = False

    # --------------------------------------------------------------- request

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._api}{path}"
        reauthed = False
        attempt = 0
        rejected: str | None = None
        while True:
            token = self._tokens.access_token(rejected)
            headers = {"Authorization": f"Bearer {token}"}
            try:
                resp = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ZoomError(f"{method} {path}: transport error: {exc}") from exc
                self._backoff(attempt, None)
                attempt += 1
                continue

            if resp.status_code == 401 and not reauthed:
                reauthed = True
                rejected = token
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._max_retries:
                    raise ZoomError(
                        f"{method} {path}: HTTP {resp.status_code} after {attempt} retries",
                        resp.status_code,
                        _body(resp),
                    )
                self._backoff(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise ZoomError(f"{method} {path}: HTTP {resp.status_code}", resp.status_code, _body(resp))
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(2.0**attempt, 30.0)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        self._sleep(delay)

    # -------------------------------------------------------------- identity

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/users/me")

    def settings(self) -> dict[str, Any]:
        return self.request("GET", "/users/me/settings")

    def verify_identity(self) -> dict[str, Any]:
        """Fail unless the token really belongs to the configured host."""
        user = self.me()
        email = (user.get("email") or "").lower()
        if email != self.host:
            raise ZoomAuthError(f"token belongs to {email or '?'}, not {self.host}; refusing to act")
        self._verified = True
        return user

    def _ensure_identity(self) -> None:
        if not self._verified:
            self.verify_identity()

    # -------------------------------------------------------------- webinars

    def list_webinars(self) -> Iterator[dict[str, Any]]:
        self._ensure_identity()
        token = ""
        while True:
            params: dict[str, Any] = {"page_size": 300, "type": "scheduled"}
            if token:
                params["next_page_token"] = token
            data = self.request("GET", "/users/me/webinars", params=params) or {}
            yield from data.get("webinars", [])
            token = data.get("next_page_token") or ""
            if not token:
                return

    def get_webinar(self, webinar_id: int | str) -> dict[str, Any]:
        self._ensure_identity()
        return self.request("GET", f"/webinars/{webinar_id}")

    def create_webinar(self, body: dict[str, Any]) -> dict[str, Any]:
        self._ensure_identity()
        return self.request("POST", "/users/me/webinars", json=body)

    def update_webinar(self, webinar_id: int | str, body: dict[str, Any]) -> None:
        self._ensure_identity()
        self.request("PATCH", f"/webinars/{webinar_id}", json=body)

    def delete_webinar(self, webinar_id: int | str) -> None:
        self._ensure_identity()
        self.request("DELETE", f"/webinars/{webinar_id}")

    def end_webinar(self, webinar_id: int | str) -> None:
        self._ensure_identity()
        self.request("PUT", f"/webinars/{webinar_id}/status", json={"action": "end"})

    def fresh_start_url(self, webinar_id: int | str) -> str:
        """start_url embeds a short-lived ZAK; always fetch it just before launch."""
        data = self.get_webinar(webinar_id)
        url = data.get("start_url")
        if not url:
            raise ZoomError(f"webinar {webinar_id}: response has no start_url", body=data)
        return url


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
