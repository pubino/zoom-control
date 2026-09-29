"""Declarative room and event specifications (apiVersion zoomcontrol/v1)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

API_VERSION = "zoomcontrol/v1"
SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- Room


class AVRouting(_Strict):
    video_device: str = Field(description="Capture device name as reported by AVFoundation")
    audio_device: str = Field(description="Audio input device name as reported by CoreAudio")
    share_display: str = Field(description="Display name (NSScreen.localizedName) to screen-share")


class Thresholds(_Strict):
    min_video_fps: float = Field(default=1.0, ge=0)
    min_audio_dbfs: float = Field(default=-70.0, le=0, description="RMS floor; quieter is 'silent'")
    black_frame_luma: float = Field(default=0.02, ge=0, le=1, description="Mean luma at or below is 'black'")


class RoomMeta(_Strict):
    id: str = Field(pattern=SLUG)
    description: str = ""


class RoomSpec(_Strict):
    runner_label: str = Field(pattern=SLUG)
    timezone: str = "America/New_York"
    zoom_host: str = Field(description="Email of the Zoom user that hosts this room's webinars; it must be "
                                       "authorized on the node with `zoomctl auth login`")
    av: AVRouting
    thresholds: Thresholds = Thresholds()
    launch_mode: Literal["zoommtg", "https"] = Field(
        default="zoommtg",
        description="How roomagent opens the start_url: zoommtg:// client scheme (default) or https via zoom.us.app",
    )

    @field_validator("zoom_host")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("zoom_host must be the host's email address")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


class Room(_Strict):
    apiVersion: Literal["zoomcontrol/v1"]
    kind: Literal["Room"]
    metadata: RoomMeta
    spec: RoomSpec

    @property
    def id(self) -> str:
        return self.metadata.id


# -------------------------------------------------------------------------- Event


class WebinarSettings(_Strict):
    """Subset of Zoom webinar settings we manage. `extra` is passed through verbatim."""

    host_video: bool = True
    panelists_video: bool = True
    practice_session: bool = False
    hd_video: bool = True
    approval_type: Literal[0, 1, 2] = 2
    auto_recording: Literal["local", "cloud", "none"] = "none"
    on_demand: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_zoom(self) -> dict[str, Any]:
        base = self.model_dump(exclude={"extra"})
        base.update(self.extra)
        return base


class Lifecycle(_Strict):
    preflight_minutes: int = Field(default=15, ge=1, le=120)
    start_url_minutes: int = Field(default=3, ge=0, le=60)
    grace_minutes: int = Field(default=10, ge=0, le=240)
    monitor_interval_seconds: int = Field(default=15, ge=5, le=300)
    max_recoveries: int = Field(default=3, ge=0, le=20)


class EventMeta(_Strict):
    uid: str = Field(pattern=SLUG, description="Stable identity; never reuse")
    name: str = Field(min_length=1, max_length=200)


class EventSpec(_Strict):
    room: str = Field(pattern=SLUG)
    start_time: datetime
    duration_minutes: int = Field(ge=5, le=24 * 60)
    agenda: str = Field(default="", max_length=1500)
    password: str | None = Field(default=None, max_length=10)
    settings: WebinarSettings = WebinarSettings()
    lifecycle: Lifecycle = Lifecycle()

    @field_validator("start_time")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("start_time must include a UTC offset, e.g. 2026-09-10T14:00:00-04:00")
        return v


class Event(_Strict):
    apiVersion: Literal["zoomcontrol/v1"]
    kind: Literal["ZoomWebinar"]
    metadata: EventMeta
    spec: EventSpec

    @property
    def uid(self) -> str:
        return self.metadata.uid

    @property
    def start(self) -> datetime:
        return self.spec.start_time.astimezone(timezone.utc)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.spec.duration_minutes)

    @property
    def run_key(self) -> str:
        """Unique per (uid, start) so a rescheduled event gets a fresh run."""
        return f"event-{self.uid}-{self.start.strftime('%Y%m%dT%H%MZ')}"


# ------------------------------------------------------------------------- Config


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Config:
    root: Path
    rooms: dict[str, Room] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)

    def room_for(self, event: Event) -> Room:
        return self.rooms[event.spec.room]

    def event(self, uid: str) -> Event:
        try:
            return self.events[uid]
        except KeyError:
            raise ConfigError([f"unknown event uid {uid!r}"]) from None


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _fmt_validation(path: Path, exc: ValidationError) -> list[str]:
    return [f"{path}: {'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()]


def load_config(root: str | Path) -> Config:
    """Load and cross-validate rooms/*.yaml and events/*.yaml. Raises ConfigError listing every problem."""
    root = Path(root)
    problems: list[str] = []
    cfg = Config(root=root)

    for kind, sub, model, store, key in (
        ("Room", "rooms", Room, cfg.rooms, "id"),
        ("ZoomWebinar", "events", Event, cfg.events, "uid"),
    ):
        folder = root / sub
        if not folder.is_dir():
            problems.append(f"{folder}: missing directory")
            continue
        for path in sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")]):
            try:
                raw = _load_yaml(path)
            except yaml.YAMLError as exc:
                problems.append(f"{path}: invalid YAML: {exc}")
                continue
            try:
                obj = model.model_validate(raw)
            except ValidationError as exc:
                problems.extend(_fmt_validation(path, exc))
                continue
            ident = getattr(obj, key)
            if ident in store:
                problems.append(f"{path}: duplicate {kind} {key} {ident!r}")
                continue
            store[ident] = obj

    if not cfg.rooms and not any("rooms" in p for p in problems):
        problems.append(f"{root / 'rooms'}: no rooms defined")

    for ev in cfg.events.values():
        if ev.spec.room not in cfg.rooms:
            problems.append(f"event {ev.uid!r}: references unknown room {ev.spec.room!r}")

    def overlaps(groups: dict[str, list[Event]], what: str) -> None:
        for key, evs in groups.items():
            evs.sort(key=lambda e: e.start)
            for a, b in zip(evs, evs[1:]):
                gap = a.end + timedelta(minutes=a.spec.lifecycle.grace_minutes)
                if b.start - timedelta(minutes=b.spec.lifecycle.preflight_minutes) < gap:
                    problems.append(f"{what} {key!r}: events {a.uid!r} and {b.uid!r} overlap "
                                    "(including grace and preflight windows)")

    known = [ev for ev in cfg.events.values() if ev.spec.room in cfg.rooms]
    by_room: dict[str, list[Event]] = {}
    by_host: dict[str, list[Event]] = {}
    for ev in known:
        by_room.setdefault(ev.spec.room, []).append(ev)
    overlaps(by_room, "room")
    # A Zoom user can host only one live webinar at a time, even across rooms.
    for ev in known:
        host = cfg.rooms[ev.spec.room].spec.zoom_host
        by_host.setdefault(host, []).append(ev)
    reported = set(problems)
    before = len(problems)
    overlaps({h: evs for h, evs in by_host.items()
              if len({e.spec.room for e in evs}) > 1}, "host")
    # drop host findings that merely repeat a same-room overlap
    problems[before:] = [p for p in problems[before:]
                         if not any(p.split(": ", 1)[1] == r.split(": ", 1)[1] for r in reported)]

    if problems:
        raise ConfigError(problems)
    return cfg


# ----------------------------------------------------------------- Zoom rendering

MARKER_RE = re.compile(r"\[zc:(?P<uid>[a-z0-9][a-z0-9-]*):(?P<hash>[0-9a-f]{12})\]")


def desired_webinar(event: Event, room: Room) -> dict[str, Any]:
    """The Zoom API body (without marker) that the event should produce."""
    body: dict[str, Any] = {
        "topic": event.metadata.name,
        "type": 5,
        "start_time": event.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": event.spec.duration_minutes,
        "timezone": room.spec.timezone,
        "settings": event.spec.settings.to_zoom(),
    }
    if event.spec.password:
        body["password"] = event.spec.password
    return body


def spec_hash(event: Event, room: Room) -> str:
    payload = {"webinar": desired_webinar(event, room), "agenda": event.spec.agenda, "host": room.spec.zoom_host}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def with_marker(event: Event, room: Room) -> dict[str, Any]:
    body = desired_webinar(event, room)
    marker = f"[zc:{event.uid}:{spec_hash(event, room)}]"
    body["agenda"] = f"{event.spec.agenda}\n\n{marker}".strip()
    return body


def parse_marker(agenda: str | None) -> tuple[str, str] | None:
    if not agenda:
        return None
    m = MARKER_RE.search(agenda)
    return (m["uid"], m["hash"]) if m else None


def json_schemas() -> dict[str, dict[str, Any]]:
    return {
        "room.v1.json": Room.model_json_schema(),
        "zoomwebinar.v1.json": Event.model_json_schema(),
    }
