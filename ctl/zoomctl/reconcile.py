"""Idempotent reconciliation of event specs against Zoom webinars.

State lives in Zoom itself: every managed webinar's agenda carries a
``[zc:<uid>:<hash>]`` marker, so there is no local state file to drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from collections.abc import Callable

from .spec import Config, Event, parse_marker, spec_hash, with_marker
from .zoom import ZoomClient

# One client per Zoom host; each can act only as that host (user-level OAuth).
ZoomFor = Callable[[str], ZoomClient]


class Action(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"
    SKIP = "skip"


@dataclass
class Change:
    action: Action
    uid: str
    host: str
    webinar_id: int | None = None
    reason: str = ""
    body: dict[str, Any] | None = field(default=None, repr=False)

    def describe(self) -> str:
        wid = f" (webinar {self.webinar_id})" if self.webinar_id else ""
        return f"{self.action.value:>6}  {self.uid}{wid} @ {self.host}: {self.reason}"


@dataclass
class ManagedWebinar:
    webinar_id: int
    uid: str
    hash: str
    host: str
    start: datetime | None


def _parse_zoom_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def managed_webinars(zoom_for: ZoomFor, hosts: set[str]) -> dict[str, list[ManagedWebinar]]:
    found: dict[str, list[ManagedWebinar]] = {}
    for host in sorted(hosts):
        zoom = zoom_for(host)
        for w in zoom.list_webinars():
            agenda = w.get("agenda")
            if agenda is None:  # some list responses omit agenda; fetch the detail
                agenda = zoom.get_webinar(w["id"]).get("agenda")
            marker = parse_marker(agenda)
            if not marker:
                continue  # not ours; never touch
            uid, h = marker
            found.setdefault(uid, []).append(
                ManagedWebinar(int(w["id"]), uid, h, host, _parse_zoom_time(w.get("start_time")))
            )
    return found


def plan(cfg: Config, zoom_for: ZoomFor, *, now: datetime, prune: bool = True) -> list[Change]:
    hosts = {r.spec.zoom_host for r in cfg.rooms.values()}
    existing = managed_webinars(zoom_for, hosts)
    changes: list[Change] = []

    for uid, ev in sorted(cfg.events.items()):
        room = cfg.room_for(ev)
        host = room.spec.zoom_host
        want = spec_hash(ev, room)
        have = existing.pop(uid, [])
        if ev.end <= now:
            changes.append(Change(Action.SKIP, uid, host, have[0].webinar_id if have else None, "event is in the past"))
            continue
        # Webinar under a different host (room reassigned) is deleted and recreated.
        same_host = [w for w in have if w.host == host]
        for stale in [w for w in have if w.host != host] + same_host[1:]:
            changes.append(Change(Action.DELETE, uid, stale.host, stale.webinar_id, "duplicate or host changed"))
        if not same_host:
            changes.append(Change(Action.CREATE, uid, host, None, "no managed webinar", with_marker(ev, room)))
        elif same_host[0].hash != want:
            changes.append(
                Change(Action.UPDATE, uid, host, same_host[0].webinar_id, "spec changed", with_marker(ev, room))
            )
        else:
            changes.append(Change(Action.NOOP, uid, host, same_host[0].webinar_id, "up to date"))

    for uid, orphans in sorted(existing.items()):
        for w in orphans:
            if prune and (w.start is None or w.start > now):
                changes.append(Change(Action.DELETE, uid, w.host, w.webinar_id, "spec removed"))
            else:
                changes.append(Change(Action.SKIP, uid, w.host, w.webinar_id, "orphan kept (past or --no-prune)"))
    return changes


def apply(changes: list[Change], zoom_for: ZoomFor) -> list[str]:
    """Apply changes; returns per-change error strings (empty list == full success)."""
    errors: list[str] = []
    for ch in changes:
        try:
            if ch.action in (Action.NOOP, Action.SKIP):
                continue
            zoom = zoom_for(ch.host)
            if ch.action is Action.CREATE:
                created = zoom.create_webinar(ch.body or {})
                ch.webinar_id = int(created["id"])
            elif ch.action is Action.UPDATE:
                zoom.update_webinar(ch.webinar_id, ch.body or {})
            elif ch.action is Action.DELETE:
                zoom.delete_webinar(ch.webinar_id)
        except Exception as exc:  # noqa: BLE001 — collect and report every failure
            errors.append(f"{ch.describe()} -> {exc}")
    return errors


def find_webinar_id(cfg: Config, zoom_for: ZoomFor, event: Event) -> int:
    """Locate the managed webinar for an event (used at run time)."""
    host = cfg.room_for(event).spec.zoom_host
    for w in managed_webinars(zoom_for, {host}).get(event.uid, []):
        if w.host == host:
            return w.webinar_id
    raise LookupError(f"no managed webinar for event {event.uid!r} under host {host!r}; run `zoomctl apply`")
