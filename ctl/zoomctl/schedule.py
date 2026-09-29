"""Dispatcher: turns upcoming events into room jobs, with dedup and a missed-start watchdog.

GitHub `schedule` triggers are best-effort and often late, so the dispatcher looks
far ahead and the room job itself sleeps until the precise lifecycle times.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .spec import Config, Event

Runner = Callable[[Sequence[str]], str]


def _default_runner(args: Sequence[str]) -> str:
    return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout


@dataclass
class Gh:
    """Thin wrapper over the `gh` CLI so tests can inject a fake runner."""

    workflow: str = "run-event.yml"
    run: Runner = _default_runner

    def runs(self, limit: int = 200) -> list[dict[str, Any]]:
        out = self.run(["gh", "run", "list", "--workflow", self.workflow, "--limit", str(limit),
                        "--json", "displayTitle,status,conclusion,databaseId,createdAt"])
        return json.loads(out or "[]")

    def dispatch(self, event: Event, room_label: str) -> None:
        self.run(["gh", "workflow", "run", self.workflow,
                  "-f", f"event={event.uid}", "-f", f"room={room_label}", "-f", f"run_key={event.run_key}"])


@dataclass
class Decision:
    uid: str
    run_key: str
    action: str  # dispatch | exists | late-dispatch | stuck-queued | run-ended
    detail: str = ""

    @property
    def alert(self) -> bool:
        return self.action in {"late-dispatch", "stuck-queued", "run-ended"}


def due_events(cfg: Config, now: datetime, lookahead: timedelta) -> list[Event]:
    """Events not yet over whose start is within the lookahead window."""
    return sorted(
        (e for e in cfg.events.values() if e.end > now and e.start <= now + lookahead),
        key=lambda e: e.start,
    )


def decide(cfg: Config, gh: Gh, now: datetime, lookahead: timedelta) -> list[Decision]:
    runs_by_key: dict[str, list[dict[str, Any]]] = {}
    for r in gh.runs():
        runs_by_key.setdefault(r.get("displayTitle", ""), []).append(r)

    decisions: list[Decision] = []
    for ev in due_events(cfg, now, lookahead):
        preflight_at = ev.start - timedelta(minutes=ev.spec.lifecycle.preflight_minutes)
        existing = runs_by_key.get(ev.run_key, [])
        if not existing:
            late = now >= preflight_at
            decisions.append(Decision(ev.uid, ev.run_key, "late-dispatch" if late else "dispatch",
                                      f"start {ev.start.isoformat()}"
                                      + (" — dispatcher missed the preflight window" if late else "")))
            continue
        statuses = {r.get("status") for r in existing}
        conclusions = {r.get("conclusion") for r in existing}
        if statuses == {"completed"} and "success" not in conclusions and now >= preflight_at:
            decisions.append(Decision(ev.uid, ev.run_key, "run-ended",
                                      f"room run finished ({','.join(sorted(c or '?' for c in conclusions))}) "
                                      "before the event ended — use manual-start if the room is down"))
        elif now >= preflight_at and statuses <= {"queued", "waiting", "pending", "requested"}:
            decisions.append(Decision(ev.uid, ev.run_key, "stuck-queued",
                                      "run still queued at preflight time — is the room runner online?"))
        else:
            decisions.append(Decision(ev.uid, ev.run_key, "exists", ",".join(sorted(s or "?" for s in statuses))))
    return decisions


def execute(cfg: Config, gh: Gh, decisions: list[Decision], *, dry_run: bool = False) -> list[str]:
    errors: list[str] = []
    for d in decisions:
        if d.action not in {"dispatch", "late-dispatch"} or dry_run:
            continue
        ev = cfg.event(d.uid)
        try:
            gh.dispatch(ev, cfg.room_for(ev).spec.runner_label)
        except subprocess.CalledProcessError as exc:
            errors.append(f"{d.uid}: gh workflow run failed: {exc.stderr or exc}")
    return errors
