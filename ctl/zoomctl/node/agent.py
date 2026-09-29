"""Wrapper around the Swift `roomagent` CLI. Every call must return a JSON report.

Missing output, unparseable output, or a crash is treated as a FAIL report —
silence never counts as success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..spec import Room

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass
class Report:
    command: str
    status: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (OK, WARN)

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def failing(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def summary(self) -> str:
        bad = [f"{c.name}={c.status}({c.detail})" for c in self.checks if c.status != OK]
        return "; ".join(bad) or (self.error or "all checks ok")

    @classmethod
    def parse(cls, command: str, raw: str) -> Report:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return cls(command, FAIL, error=f"roomagent returned no/invalid JSON: {raw[:200]!r}")
        if not isinstance(data, dict) or data.get("status") not in (OK, WARN, FAIL):
            return cls(command, FAIL, error=f"roomagent JSON missing valid status: {raw[:200]!r}")
        checks = [Check(c.get("name", "?"), c.get("status", FAIL), c.get("detail", "")) for c in data.get("checks", [])]
        return cls(data.get("command", command), data["status"], checks, data.get("error"))

    @classmethod
    def failure(cls, command: str, error: str) -> Report:
        return cls(command, FAIL, error=error)


class Agent(Protocol):
    def preflight(self, room: Room) -> Report: ...
    def launch(self, start_url: str, mode: str = "zoommtg") -> Report: ...
    def share(self, room: Room) -> Report: ...
    def select_av(self, room: Room) -> Report: ...
    def health(self, room: Room) -> Report: ...
    def quit(self, force: bool = False) -> Report: ...


def room_args(room: Room) -> list[str]:
    av, th = room.spec.av, room.spec.thresholds
    return [
        "--video-device", av.video_device,
        "--audio-device", av.audio_device,
        "--display", av.share_display,
        "--min-fps", str(th.min_video_fps),
        "--min-dbfs", str(th.min_audio_dbfs),
        "--black-luma", str(th.black_frame_luma),
    ]


def find_roomagent(explicit: str | None = None) -> str | None:
    for candidate in (explicit, os.environ.get("ROOMAGENT"), shutil.which("roomagent"), "/usr/local/bin/roomagent"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


@dataclass
class RoomAgentCLI:
    binary: str
    timeout: float = 120.0

    def _run(self, command: str, args: list[str], stdin: str | None = None) -> Report:
        try:
            proc = subprocess.run(
                [self.binary, command, *args],
                input=stdin, capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return Report.failure(command, f"roomagent {command} timed out after {self.timeout}s")
        except OSError as exc:
            return Report.failure(command, f"roomagent {command} could not start: {exc}")
        report = Report.parse(command, proc.stdout.strip())
        if proc.returncode not in (0, 2) and report.status != FAIL:
            report = Report.failure(command, f"exit {proc.returncode}: {proc.stderr.strip()[:300]}")
        elif report.error is None and proc.returncode != 0 and proc.stderr:
            report.error = proc.stderr.strip()[:300]
        return report

    def preflight(self, room: Room) -> Report:
        return self._run("preflight", room_args(room))

    def launch(self, start_url: str, mode: str = "zoommtg") -> Report:
        # URL carries a ZAK token: pass on stdin, never argv (visible in `ps`).
        return self._run("launch", ["--start-url-stdin", "--mode", mode], stdin=start_url + "\n")

    def share(self, room: Room) -> Report:
        return self._run("share", room_args(room))

    def select_av(self, room: Room) -> Report:
        return self._run("select-av", room_args(room))

    def health(self, room: Room) -> Report:
        return self._run("health", room_args(room))

    def quit(self, force: bool = False) -> Report:
        return self._run("quit", ["--force"] if force else [])


def as_dict(report: Report) -> dict[str, Any]:
    return {"command": report.command, "status": report.status, "error": report.error,
            "checks": [c.__dict__ for c in report.checks]}
