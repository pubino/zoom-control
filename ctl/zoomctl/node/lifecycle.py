"""On-node event lifecycle: wait → preflight → launch → monitor/recover → teardown.

All timing is driven by an injectable Clock so the full state machine is testable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from ..notify import Level, Notifier
from ..spec import Event, Room
from .agent import Agent, Report

RELAUNCH_CHECKS = ("zoom_running", "in_meeting")
RESHARE_CHECKS = ("sharing",)


class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class KeepAwake(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class Caffeinate:
    """Holds `caffeinate -dimsu` for this process's lifetime (plan.txt's `caffeine` does not exist)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        exe = shutil.which("caffeinate")
        if exe and self._proc is None:
            self._proc = subprocess.Popen([exe, "-dimsu", "-w", str(os.getpid())])

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None


class NoopKeepAwake:
    def start(self) -> None: ...
    def stop(self) -> None: ...


class Phase(StrEnum):
    WAITING = "waiting"
    PREFLIGHT = "preflight"
    LAUNCHING = "launching"
    LIVE = "live"
    TEARDOWN = "teardown"
    DONE = "done"


class Outcome(StrEnum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class ZoomOps:
    """The Zoom API operations the lifecycle needs (injected for testability)."""

    start_url: Callable[[], str]
    end: Callable[[], None]


@dataclass
class RunResult:
    outcome: Outcome
    recoveries: int = 0
    degraded_reasons: list[str] = field(default_factory=list)
    error: str | None = None
    phases: list[Phase] = field(default_factory=list)


@dataclass
class Lifecycle:
    event: Event
    room: Room
    agent: Agent
    zoom: ZoomOps
    notifier: Notifier
    clock: Clock = field(default_factory=SystemClock)
    keep_awake: KeepAwake = field(default_factory=NoopKeepAwake)
    immediate: bool = False
    launch_attempts: int = 3

    result: RunResult = field(init=False)
    _alerted: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.result = RunResult(Outcome.SUCCESS)

    # ------------------------------------------------------------- helpers

    @property
    def lc(self):
        return self.event.spec.lifecycle

    def _phase(self, p: Phase) -> None:
        self.result.phases.append(p)

    def _wait_until(self, when: datetime) -> None:
        if self.immediate:
            return
        while (remaining := (when - self.clock.now()).total_seconds()) > 0:
            self.clock.sleep(min(remaining, 60.0))

    def _degrade(self, reason: str) -> None:
        if self.result.outcome is Outcome.SUCCESS:
            self.result.outcome = Outcome.DEGRADED
        self.result.degraded_reasons.append(reason)

    def _alert_once(self, key: str, level: Level, title: str, detail: str = "") -> None:
        if key not in self._alerted:
            self._alerted.add(key)
            self.notifier.send(level, title, detail)

    def _clear(self, key: str) -> None:
        if key in self._alerted:
            self._alerted.discard(key)
            self.notifier.send(Level.INFO, f"recovered: {key}")

    # ----------------------------------------------------------------- run

    def run(self) -> RunResult:
        ev = self.event
        now = self.clock.now()
        hard_stop = ev.end + timedelta(minutes=self.lc.grace_minutes)
        if now >= ev.end and not self.immediate:
            self.result.outcome = Outcome.FAILED
            self.result.error = f"event already ended at {ev.end.isoformat()}; refusing to start"
            self.notifier.send(Level.CRITICAL, "event missed entirely", self.result.error)
            return self.result

        self.keep_awake.start()
        try:
            self._phase(Phase.WAITING)
            self._wait_until(ev.start - timedelta(minutes=self.lc.preflight_minutes))
            self._preflight()

            self._phase(Phase.LAUNCHING)
            self._wait_until(ev.start - timedelta(minutes=self.lc.start_url_minutes))
            if not self._go_live():
                self.result.outcome = Outcome.FAILED
                self.result.error = self.result.error or "could not go live"
                self.notifier.send(Level.CRITICAL, "failed to start webinar", self.result.error)
            else:
                self._phase(Phase.LIVE)
                self.notifier.send(Level.INFO, "webinar live")
                end_at = hard_stop if not self.immediate else self.clock.now() + timedelta(
                    minutes=ev.spec.duration_minutes + self.lc.grace_minutes)
                self._monitor(end_at)
        except Exception as exc:  # noqa: BLE001 — surface everything, then teardown
            self.result.outcome = Outcome.FAILED
            self.result.error = f"lifecycle crashed: {exc!r}"
            self.notifier.send(Level.CRITICAL, "lifecycle crashed", repr(exc))
        finally:
            self._teardown()
            self.keep_awake.stop()
            self._phase(Phase.DONE)
            self._final_report()
        return self.result

    # ------------------------------------------------------------ phases

    def _preflight(self) -> None:
        self._phase(Phase.PREFLIGHT)
        deadline = self.event.start - timedelta(minutes=self.lc.start_url_minutes)
        attempt = 0
        while True:
            attempt += 1
            rep = self.agent.preflight(self.room)
            if rep.status == "ok":
                if attempt > 1:
                    self._clear("preflight")
                return
            if rep.status == "warn":
                self._alert_once("preflight-warn", Level.WARN, "preflight warnings", rep.summary())
                return
            self._alert_once("preflight", Level.CRITICAL, "preflight failed — retrying until launch time",
                             rep.summary())
            if self.immediate or self.clock.now() + timedelta(seconds=60) >= deadline:
                self._degrade(f"preflight: {rep.summary()}")
                return  # best effort: still try to go live
            self.clock.sleep(60)

    def _go_live(self) -> bool:
        for attempt in range(1, self.launch_attempts + 1):
            try:
                url = self.zoom.start_url()
            except Exception as exc:  # noqa: BLE001
                self.result.error = f"start_url fetch failed: {exc}"
                self.notifier.send(Level.WARN, f"start_url fetch failed (attempt {attempt})", str(exc))
                self.clock.sleep(10 * attempt)
                continue
            launched = self.agent.launch(url)
            if not launched.ok:
                self.result.error = f"launch failed: {launched.summary()}"
                self.notifier.send(Level.WARN, f"launch failed (attempt {attempt})", launched.summary())
                self.agent.quit(force=True)
                self.clock.sleep(10 * attempt)
                continue
            self._share_and_route()
            return True
        return False

    def _share_and_route(self) -> None:
        shared = self.agent.share(self.room)
        if not shared.ok:
            self._alert_once("sharing", Level.WARN, "screen share failed", shared.summary())
        av = self.agent.select_av(self.room)
        if not av.ok:
            self._alert_once("select-av", Level.WARN, "AV routing failed", av.summary())
            self._degrade(f"select-av: {av.summary()}")

    def _relaunch(self, why: str) -> bool:
        self.result.recoveries += 1
        self.notifier.send(Level.WARN, f"recovery {self.result.recoveries}/{self.lc.max_recoveries}: relaunch", why)
        self.agent.quit(force=True)
        return self._go_live()

    def _monitor(self, until: datetime) -> None:
        interval = self.lc.monitor_interval_seconds
        while self.clock.now() < until:
            self.clock.sleep(interval)
            rep = self.agent.health(self.room)
            if rep.error and not rep.checks:
                self._alert_once("health", Level.WARN, "health probe failed", rep.error)
                continue
            self._clear("health")

            failing = {c.name: c for c in rep.failing()}
            if any(n in failing for n in RELAUNCH_CHECKS):
                why = "; ".join(f"{n}: {failing[n].detail}" for n in RELAUNCH_CHECKS if n in failing)
                if self.result.recoveries >= self.lc.max_recoveries:
                    self.result.outcome = Outcome.FAILED
                    self.result.error = f"recovery budget exhausted ({self.lc.max_recoveries}); last: {why}"
                    self.notifier.send(Level.CRITICAL, "recovery budget exhausted — manual intervention needed", why)
                    return
                if not self._relaunch(why):
                    self.result.outcome = Outcome.FAILED
                    self.result.error = f"relaunch failed: {why}"
                    self.notifier.send(Level.CRITICAL, "relaunch failed — webinar is down", why)
                    return
                continue

            if any(n in failing for n in RESHARE_CHECKS):
                if self.result.recoveries >= self.lc.max_recoveries:
                    self._alert_once("sharing", Level.CRITICAL, "screen share lost; recovery budget exhausted")
                    self._degrade("screen share lost")
                else:
                    self.result.recoveries += 1
                    self.notifier.send(Level.WARN, f"recovery {self.result.recoveries}/{self.lc.max_recoveries}: "
                                                   "re-sharing screen", failing["sharing"].detail)
                    if self.agent.share(self.room).ok:
                        self._clear("sharing")
            else:
                self._clear("sharing")

            for name in sorted(set(failing) - set(RELAUNCH_CHECKS) - set(RESHARE_CHECKS)):
                if name not in self._alerted:
                    self._degrade(f"{name}: {failing[name].detail}")
                self._alert_once(name, Level.WARN, f"health: {name} failing", failing[name].detail)
            for name in list(self._alerted):
                if name not in failing and name not in {"preflight", "preflight-warn", "select-av", "sharing",
                                                        "health"}:
                    self._clear(name)

    def _teardown(self) -> None:
        self._phase(Phase.TEARDOWN)
        try:
            self.zoom.end()
        except Exception as exc:  # noqa: BLE001 — already ended is common and fine
            self.notifier.send(Level.INFO, "API end webinar returned an error (may already be ended)", str(exc))
        q: Report = self.agent.quit(force=False)
        if not q.ok:
            q = self.agent.quit(force=True)
            if not q.ok:
                self._degrade(f"quit: {q.summary()}")
                self.notifier.send(Level.CRITICAL, "could not quit Zoom — room not reset", q.summary())

    def _final_report(self) -> None:
        r = self.result
        level = {Outcome.SUCCESS: Level.INFO, Outcome.DEGRADED: Level.WARN, Outcome.FAILED: Level.CRITICAL}[r.outcome]
        detail = f"recoveries={r.recoveries}"
        if r.degraded_reasons:
            detail += "; degraded: " + " | ".join(r.degraded_reasons)
        if r.error:
            detail += f"; error: {r.error}"
        self.notifier.send(level, f"event finished: {r.outcome.value}", detail)
