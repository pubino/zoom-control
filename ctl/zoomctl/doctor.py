"""`zoomctl check`: verify each room host's user-level authorization before going live."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .spec import Config
from .zoom import ZoomClient, ZoomError
from .zoom.tokens import TokenStore, normalize_host

OK, WARN, FAIL = "ok", "warn", "fail"
REFRESH_WARN_DAYS = 60  # Zoom expires a refresh token after 90 days unused


@dataclass
class Finding:
    status: str
    subject: str
    detail: str

    def line(self) -> str:
        icon = {OK: "✓", WARN: "⚠", FAIL: "✗"}[self.status]
        return f"{icon} {self.subject}: {self.detail}"


def _zoom_message(exc: ZoomError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    msg = body.get("message") or body.get("reason") or str(exc)
    code = body.get("code")
    return f"{msg} (Zoom code {code})" if code else msg


def check(cfg: Config, zoom_for: Callable[[str], ZoomClient], store: TokenStore,
          clock: Callable[[], float] = time.time) -> list[Finding]:
    findings: list[Finding] = [Finding(OK, "token store", store.describe())]
    hosts = sorted({normalize_host(r.spec.zoom_host) for r in cfg.rooms.values()})
    for host in hosts:
        rooms = ", ".join(sorted(r.id for r in cfg.rooms.values() if normalize_host(r.spec.zoom_host) == host))
        subject = f"{host} ({rooms})"
        try:
            rec = store.load(host)
        except ZoomError as exc:
            findings.append(Finding(FAIL, subject, str(exc)))
            continue
        if rec is None:
            findings.append(Finding(FAIL, subject, f"not authorized on this node — run: zoomctl auth login --host {host}"))
            continue
        zoom = zoom_for(host)
        try:
            user = zoom.verify_identity()  # also exercises refresh
        except ZoomError as exc:
            findings.append(Finding(FAIL, subject, _zoom_message(exc)))
            continue
        findings.append(Finding(OK, subject, f"authorized as {user.get('email')}"))
        rec = store.load(host) or rec
        age = (clock() - rec.refreshed_at) / 86400 if rec.refreshed_at else 0
        if age > REFRESH_WARN_DAYS:
            findings.append(Finding(WARN, f"{subject} refresh token", f"unrotated for {age:.0f} days (expires at 90)"))
        if user.get("type") == 1:
            findings.append(Finding(FAIL, subject, "user is Basic (unlicensed); webinars need a licensed host"))
            continue
        try:
            feature = (zoom.settings() or {}).get("feature", {})
        except ZoomError as exc:
            findings.append(Finding(WARN, subject, f"cannot read settings ({_zoom_message(exc)}) — "
                                                   "add user:read:settings scope? license not verified"))
        else:
            if feature.get("webinar") is True:
                cap = feature.get("webinar_capacity")
                findings.append(Finding(OK, subject, f"webinar license{f' ({cap} attendees)' if cap else ''}"))
            elif feature.get("webinar") is False:
                findings.append(Finding(FAIL, subject, "no Webinar license assigned"))
                continue
            else:
                findings.append(Finding(WARN, subject, "settings do not report a webinar feature flag"))
        try:
            next(iter(zoom.list_webinars()), None)
            findings.append(Finding(OK, f"{subject} webinars", "list permitted"))
        except ZoomError as exc:
            findings.append(Finding(FAIL, f"{subject} webinars", f"{_zoom_message(exc)} — missing webinar scopes?"))
    extra = sorted(set(store.hosts()) - set(hosts))
    if extra:
        findings.append(Finding(WARN, "token store", f"authorizations for hosts not in config: {', '.join(extra)} "
                                                     "(zoomctl auth logout --host …)"))
    return findings
