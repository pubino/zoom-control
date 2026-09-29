"""`zoomctl check`: verify Zoom credentials, scopes and every room host before going live."""

from __future__ import annotations

from dataclasses import dataclass

from .spec import Config
from .zoom import ZoomClient, ZoomError

OK, WARN, FAIL = "ok", "warn", "fail"


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


def check(cfg: Config, zoom: ZoomClient) -> list[Finding]:
    findings: list[Finding] = []
    try:
        zoom._access_token(force=True)
        findings.append(Finding(OK, "credentials", "S2S OAuth token issued"))
    except ZoomError as exc:
        findings.append(Finding(FAIL, "credentials", f"{_zoom_message(exc)} — check account/client id/secret "
                                                     "and that the app is Activated"))
        return findings

    hosts = sorted({r.spec.zoom_host for r in cfg.rooms.values()})
    for host in hosts:
        rooms = ", ".join(sorted(r.id for r in cfg.rooms.values() if r.spec.zoom_host == host))
        subject = f"host {host} ({rooms})"
        try:
            user = zoom.get_user(host)
        except ZoomError as exc:
            hint = " — missing user:read scope?" if exc.status in (400, 401, 403) else ""
            findings.append(Finding(FAIL, subject, f"{_zoom_message(exc)}{hint}"))
            continue
        if user.get("status") not in (None, "active"):
            findings.append(Finding(FAIL, subject, f"user status is {user.get('status')!r}"))
            continue
        if user.get("type") == 1:
            findings.append(Finding(FAIL, subject, "user is Basic (unlicensed); webinars need a licensed host"))
            continue
        try:
            feature = (zoom.get_user_settings(host) or {}).get("feature", {})
        except ZoomError as exc:
            findings.append(Finding(WARN, subject, f"cannot read settings ({_zoom_message(exc)}); "
                                                   "webinar license not verified"))
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
            next(iter(zoom.list_webinars(host)), None)
            findings.append(Finding(OK, f"{subject} webinars", "list permitted"))
        except ZoomError as exc:
            findings.append(Finding(FAIL, f"{subject} webinars", f"{_zoom_message(exc)} — missing webinar scopes?"))
    return findings
