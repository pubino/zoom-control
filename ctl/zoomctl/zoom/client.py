"""Zoom REST client using Server-to-Server OAuth, with retry/backoff on 429/5xx."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_API = "https://api.zoom.us/v2"
DEFAULT_OAUTH = "https://zoom.us/oauth/token"


class ZoomError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class Credentials:
    account_id: str
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Credentials:
        env = dict(os.environ if env is None else env)
        missing = [k for k in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET") if not env.get(k)]
        if missing:
            raise ZoomError(f"missing Zoom credentials in environment: {', '.join(missing)}")
        return cls(env["ZOOM_ACCOUNT_ID"], env["ZOOM_CLIENT_ID"], env["ZOOM_CLIENT_SECRET"])


class ZoomClient:
    def __init__(
        self,
        creds: Credentials,
        *,
        api_base: str | None = None,
        oauth_url: str | None = None,
        http: httpx.Client | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._creds = creds
        self._api = (api_base or os.environ.get("ZOOM_API_BASE") or DEFAULT_API).rstrip("/")
        self._oauth = oauth_url or os.environ.get("ZOOM_OAUTH_URL") or DEFAULT_OAUTH
        self._http = http or httpx.Client(timeout=httpx.Timeout(20.0))
        self._max_retries = max_retries
        self._sleep = sleep
        self._monotonic = monotonic
        self._token: str | None = None
        self._token_expiry = 0.0

    # ------------------------------------------------------------------ auth

    def _access_token(self, force: bool = False) -> str:
        if not force and self._token and self._monotonic() < self._token_expiry - 60:
            return self._token
        resp = self._http.post(
            self._oauth,
            params={"grant_type": "account_credentials", "account_id": self._creds.account_id},
            auth=(self._creds.client_id, self._creds.client_secret),
        )
        if resp.status_code != 200:
            raise ZoomError(f"OAuth token request failed: HTTP {resp.status_code}", resp.status_code, _body(resp))
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ZoomError("OAuth response missing access_token", resp.status_code, data)
        self._token = token
        self._token_expiry = self._monotonic() + float(data.get("expires_in", 3600))
        return token

    # --------------------------------------------------------------- request

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._api}{path}"
        reauthed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self._access_token()}"}
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
                self._access_token(force=True)
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

    # ----------------------------------------------------------------- users

    def get_user(self, user: str) -> dict[str, Any]:
        return self.request("GET", f"/users/{user}")

    def get_user_settings(self, user: str) -> dict[str, Any]:
        return self.request("GET", f"/users/{user}/settings")

    # -------------------------------------------------------------- webinars

    def list_webinars(self, user: str) -> Iterator[dict[str, Any]]:
        token = ""
        while True:
            params: dict[str, Any] = {"page_size": 300, "type": "scheduled"}
            if token:
                params["next_page_token"] = token
            data = self.request("GET", f"/users/{user}/webinars", params=params) or {}
            yield from data.get("webinars", [])
            token = data.get("next_page_token") or ""
            if not token:
                return

    def get_webinar(self, webinar_id: int | str) -> dict[str, Any]:
        return self.request("GET", f"/webinars/{webinar_id}")

    def create_webinar(self, user: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/users/{user}/webinars", json=body)

    def update_webinar(self, webinar_id: int | str, body: dict[str, Any]) -> None:
        self.request("PATCH", f"/webinars/{webinar_id}", json=body)

    def delete_webinar(self, webinar_id: int | str) -> None:
        self.request("DELETE", f"/webinars/{webinar_id}")

    def end_webinar(self, webinar_id: int | str) -> None:
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
