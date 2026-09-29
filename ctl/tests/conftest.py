from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "config"


def pytest_collection_finish(session):
    # Silence is never success: an empty test run must fail loudly.
    if not session.items:
        raise pytest.UsageError("no tests collected")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(EXAMPLES, dst)
    return dst


def write_event(config_dir: Path, uid: str, start: datetime, **spec: Any) -> Path:
    doc = {
        "apiVersion": "zoomcontrol/v1",
        "kind": "ZoomWebinar",
        "metadata": {"uid": uid, "name": f"Event {uid}"},
        "spec": {"room": "room-101", "start_time": start.isoformat(), "duration_minutes": 60, **spec},
    }
    path = config_dir / "events" / f"{uid}.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


class _HostView:
    """What a per-host ZoomClient exposes; delegates to the shared fake account."""

    def __init__(self, acct: "FakeZoom", host: str):
        self.acct, self.host = acct, host

    def list_webinars(self):
        return self.acct.list_webinars(self.host)

    def create_webinar(self, body):
        return self.acct.create_webinar(self.host, body)

    def __getattr__(self, name):
        return getattr(self.acct, name)


class FakeZoom:
    """In-memory stand-in for a Zoom account; `zoom_for(host)` gives the per-host client view."""

    def zoom_for(self, host: str) -> _HostView:
        self.calls.append(("client", host))
        return _HostView(self, host)

    def __init__(self) -> None:
        self.webinars: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, Any]] = []
        self._next = 1000
        self.ended: list[int] = []

    def add(self, host: str, **w: Any) -> int:
        self._next += 1
        self.webinars[self._next] = {"id": self._next, "host": host, **w}
        return self._next

    def list_webinars(self, user: str):
        self.calls.append(("list", user))
        for w in self.webinars.values():
            if w["host"] == user:
                yield {k: v for k, v in w.items() if k != "host"}

    def get_webinar(self, wid):
        self.calls.append(("get", wid))
        return {**self.webinars[int(wid)], "start_url": f"https://zoom.us/s/{wid}?zak=fresh"}

    def create_webinar(self, user, body):
        self.calls.append(("create", user))
        wid = self.add(user, **body)
        return {"id": wid}

    def update_webinar(self, wid, body):
        self.calls.append(("update", wid))
        self.webinars[int(wid)].update(body)

    def delete_webinar(self, wid):
        self.calls.append(("delete", wid))
        del self.webinars[int(wid)]

    def end_webinar(self, wid):
        self.ended.append(int(wid))

    def fresh_start_url(self, wid):
        return self.get_webinar(wid)["start_url"]

    def mutations(self):
        return [c for c in self.calls if c[0] in ("create", "update", "delete")]


@pytest.fixture
def fake_zoom() -> FakeZoom:
    return FakeZoom()
