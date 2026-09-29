from __future__ import annotations

from typing import Any


class ZoomError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ZoomAuthError(ZoomError):
    """Authorization problem (missing/revoked/expired user token, keychain unavailable)."""
