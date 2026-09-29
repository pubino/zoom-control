from datetime import datetime, timedelta, timezone

import pytest

from zoomctl.node.agent import Check, Report
from zoomctl.node.lifecycle import Lifecycle, Outcome, Phase, ZoomOps
from zoomctl.notify import Level, RecordingNotifier
from zoomctl.spec import load_config

START = datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc)
HEALTHY = ["zoom_running", "in_meeting", "sharing", "video_signal", "audio_signal", "display_not_black"]


class FakeClock:
    def __init__(self, now):
        self.t = now
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)
        self.slept += s


def rep(cmd, status="ok", failing=(), error=None):
    checks = [Check(n, "fail" if n in failing else "ok") for n in (HEALTHY if cmd == "health" else [cmd])]
    if status == "fail" and not failing and cmd != "health":
        checks = [Check(cmd, "fail", "broken")]
    return Report(cmd, "fail" if (failing or status == "fail") else status, checks, error)


class FakeAgent:
    """Scriptable roomagent: health() pops from a script of failing-check sets, then stays healthy."""

    def __init__(self, preflight=None, launch=None, health_script=None, share=None, quit_ok=True):
        self.preflight_q = list(preflight or [rep("preflight")])
        self.launch_q = list(launch or [])
        self.share_q = list(share or [])
        self.health_q = list(health_script or [])
        self.quit_ok = quit_ok
        self.calls = []
        self.urls = []

    def preflight(self, room):
        self.calls.append("preflight")
        return self.preflight_q.pop(0) if len(self.preflight_q) > 1 else self.preflight_q[0]

    def launch(self, url):
        self.calls.append("launch")
        self.urls.append(url)
        return self.launch_q.pop(0) if self.launch_q else rep("launch")

    def share(self, room):
        self.calls.append("share")
        return self.share_q.pop(0) if self.share_q else rep("share")

    def select_av(self, room):
        self.calls.append("select-av")
        return rep("select-av")

    def health(self, room):
        self.calls.append("health")
        failing = self.health_q.pop(0) if self.health_q else ()
        return rep("health", failing=failing)

    def quit(self, force=False):
        self.calls.append("quit!" if force else "quit")
        return rep("quit", "ok" if self.quit_ok else "fail")


class FakeZoomOps:
    def __init__(self, fail_first=0):
        self.n = 0
        self.ended = 0
        self.fail_first = fail_first

    def ops(self):
        def start_url():
            self.n += 1
            if self.n <= self.fail_first:
                raise RuntimeError("zoom api 503")
            return f"https://zoom.us/s/1?zak=token{self.n}"

        def end():
            self.ended += 1

        return ZoomOps(start_url, end)


@pytest.fixture
def ctx(config_dir):
    cfg = load_config(config_dir)
    ev = cfg.event("cs101-guest-2026-09-10")
    return ev, cfg.room_for(ev)


def run(ctx, agent, *, now=START - timedelta(hours=1), zoom=None, **kw):
    ev, room = ctx
    zoom = zoom or FakeZoomOps()
    notifier = RecordingNotifier()
    clock = FakeClock(now)
    lc = Lifecycle(ev, room, agent, zoom.ops(), notifier, clock=clock, **kw)
    return lc.run(), notifier, clock, zoom


def test_happy_path_timing_and_teardown(ctx):
    agent = FakeAgent()
    res, notes, clock, zoom = run(ctx, agent)
    assert res.outcome is Outcome.SUCCESS and res.recoveries == 0
    assert res.phases == [Phase.WAITING, Phase.PREFLIGHT, Phase.LAUNCHING, Phase.LIVE, Phase.TEARDOWN, Phase.DONE]
    assert agent.calls[:4] == ["preflight", "launch", "share", "select-av"]
    assert agent.calls[-1] == "quit"
    assert zoom.ended == 1 and zoom.n == 1
    # monitored until end + grace
    assert clock.now() >= START + timedelta(minutes=70)
    assert notes.titles(Level.CRITICAL) == []
    assert notes.titles()[-1] == "event finished: success"


def test_start_url_is_fetched_fresh_for_each_launch(ctx):
    agent = FakeAgent(health_script=[("zoom_running", "in_meeting")])
    res, *_ = run(ctx, agent)
    assert res.recoveries == 1 and res.outcome is Outcome.SUCCESS
    assert agent.urls == ["https://zoom.us/s/1?zak=token1", "https://zoom.us/s/1?zak=token2"]


def test_crash_relaunches_and_alerts(ctx):
    agent = FakeAgent(health_script=[(), ("zoom_running",)])
    res, notes, *_ = run(ctx, agent)
    assert res.recoveries == 1
    assert agent.calls.count("launch") == 2 and "quit!" in agent.calls
    assert any("relaunch" in t for t in notes.titles(Level.WARN))


def test_lost_share_is_reshared(ctx):
    agent = FakeAgent(health_script=[("sharing",)])
    res, notes, *_ = run(ctx, agent)
    assert res.recoveries == 1 and res.outcome is Outcome.SUCCESS
    assert agent.calls.count("share") == 2 and agent.calls.count("launch") == 1
    assert any("re-sharing" in t for t in notes.titles(Level.WARN))


def test_recovery_budget_exhausted_fails_loudly(ctx):
    agent = FakeAgent(health_script=[("zoom_running",)] * 10)
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.FAILED and res.recoveries == 3
    assert "recovery budget exhausted" in res.error
    assert any("budget exhausted" in t for t in notes.titles(Level.CRITICAL))
    assert notes.titles()[-1] == "event finished: failed"


def test_preflight_failure_retries_then_proceeds_degraded(ctx):
    agent = FakeAgent(preflight=[rep("preflight", "fail")])
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.DEGRADED
    assert agent.calls.count("preflight") > 1 and "launch" in agent.calls
    assert notes.titles(Level.CRITICAL) == ["preflight failed — retrying until launch time"]


def test_preflight_recovers(ctx):
    agent = FakeAgent(preflight=[rep("preflight", "fail"), rep("preflight", "fail"), rep("preflight")])
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.SUCCESS
    assert "recovered: preflight" in notes.titles(Level.INFO)


def test_launch_never_succeeds(ctx):
    agent = FakeAgent(launch=[rep("launch", "fail")] * 5)
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.FAILED and agent.calls.count("launch") == 3
    assert "failed to start webinar" in notes.titles(Level.CRITICAL)
    assert agent.calls[-1] == "quit"  # teardown still runs


def test_zoom_api_outage_is_retried(ctx):
    agent = FakeAgent()
    res, notes, _, zoom = run(ctx, agent, zoom=FakeZoomOps(fail_first=2))
    assert res.outcome is Outcome.SUCCESS and zoom.n == 3
    assert len([t for t in notes.titles(Level.WARN) if "start_url" in t]) == 2


def test_hardware_signal_loss_degrades_and_alerts_once(ctx):
    agent = FakeAgent(health_script=[("audio_signal",)] * 4)
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.DEGRADED and res.recoveries == 0
    assert notes.titles(Level.WARN).count("health: audio_signal failing") == 1
    assert "recovered: audio_signal" in notes.titles(Level.INFO)
    assert len(res.degraded_reasons) == 1


def test_event_already_over_refuses(ctx):
    agent = FakeAgent()
    res, notes, *_ = run(ctx, agent, now=START + timedelta(hours=2))
    assert res.outcome is Outcome.FAILED and agent.calls == []
    assert notes.titles(Level.CRITICAL) == ["event missed entirely"]


def test_immediate_skips_waits(ctx):
    agent = FakeAgent()
    res, _, clock, _ = run(ctx, agent, now=START - timedelta(days=3), immediate=True)
    assert res.outcome is Outcome.SUCCESS
    assert agent.calls[:2] == ["preflight", "launch"]
    assert clock.slept < 2 * 3600  # never waited days for the scheduled time


def test_quit_failure_is_critical(ctx):
    agent = FakeAgent(quit_ok=False)
    res, notes, *_ = run(ctx, agent)
    assert res.outcome is Outcome.DEGRADED
    assert "could not quit Zoom — room not reset" in notes.titles(Level.CRITICAL)
    assert agent.calls[-2:] == ["quit", "quit!"]


def test_agent_crash_inside_lifecycle_still_tears_down(ctx):
    agent = FakeAgent()

    def explode(room):
        raise OSError("roomagent vanished")

    agent.share = explode
    res, notes, _, zoom = run(ctx, agent)
    assert res.outcome is Outcome.FAILED and "lifecycle crashed" in res.error
    assert zoom.ended == 1 and agent.calls[-1] == "quit"
