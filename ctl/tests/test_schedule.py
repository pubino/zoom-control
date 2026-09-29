import json
import subprocess
from datetime import datetime, timedelta, timezone

from zoomctl.schedule import Gh, decide, due_events, execute
from zoomctl.spec import load_config

START = datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc)
KEY = "event-cs101-guest-2026-09-10-20260910T1800Z"


class FakeGh:
    def __init__(self, runs=None, fail_dispatch=False):
        self.runs = runs or []
        self.calls = []
        self.fail_dispatch = fail_dispatch

    def __call__(self, args):
        self.calls.append(list(args))
        if args[1:3] == ["run", "list"]:
            return json.dumps(self.runs)
        if args[1:3] == ["workflow", "run"]:
            if self.fail_dispatch:
                raise subprocess.CalledProcessError(1, args, stderr="HTTP 403")
            return ""
        raise AssertionError(f"unexpected gh call {args}")

    def dispatched(self):
        return [c for c in self.calls if c[1:3] == ["workflow", "run"]]


def test_due_window(config_dir):
    cfg = load_config(config_dir)
    assert due_events(cfg, START - timedelta(hours=4), timedelta(hours=3)) == []
    assert [e.uid for e in due_events(cfg, START - timedelta(hours=2), timedelta(hours=3))] == [
        "cs101-guest-2026-09-10"]
    assert [e.uid for e in due_events(cfg, START + timedelta(minutes=30), timedelta(hours=3))]  # still running
    assert due_events(cfg, START + timedelta(hours=1), timedelta(hours=3)) == []


def test_dispatches_once_with_room_label(config_dir):
    cfg = load_config(config_dir)
    fake = FakeGh()
    gh = Gh(run=fake)
    ds = decide(cfg, gh, START - timedelta(hours=2), timedelta(hours=3))
    assert [d.action for d in ds] == ["dispatch"] and not ds[0].alert
    assert execute(cfg, gh, ds) == []
    assert fake.dispatched() == [["gh", "workflow", "run", "run-event.yml", "-f", "event=cs101-guest-2026-09-10",
                                  "-f", "room=room-101", "-f", f"run_key={KEY}"]]


def test_existing_run_is_not_redispatched(config_dir):
    cfg = load_config(config_dir)
    fake = FakeGh(runs=[{"displayTitle": KEY, "status": "queued"}])
    gh = Gh(run=fake)
    ds = decide(cfg, gh, START - timedelta(hours=2), timedelta(hours=3))
    assert [d.action for d in ds] == ["exists"]
    execute(cfg, gh, ds)
    assert fake.dispatched() == []


def test_late_dispatch_alerts(config_dir):
    cfg = load_config(config_dir)
    ds = decide(cfg, Gh(run=FakeGh()), START - timedelta(minutes=5), timedelta(hours=3))
    assert [d.action for d in ds] == ["late-dispatch"] and ds[0].alert


def test_stuck_queued_alerts_but_in_progress_does_not(config_dir):
    cfg = load_config(config_dir)
    at = START - timedelta(minutes=10)
    stuck = decide(cfg, Gh(run=FakeGh(runs=[{"displayTitle": KEY, "status": "queued"}])), at, timedelta(hours=3))
    assert stuck[0].action == "stuck-queued" and stuck[0].alert
    ok = decide(cfg, Gh(run=FakeGh(runs=[{"displayTitle": KEY, "status": "in_progress"}])), at, timedelta(hours=3))
    assert ok[0].action == "exists" and not ok[0].alert


def test_dry_run_and_dispatch_errors(config_dir):
    cfg = load_config(config_dir)
    fake = FakeGh(fail_dispatch=True)
    gh = Gh(run=fake)
    ds = decide(cfg, gh, START - timedelta(hours=1), timedelta(hours=3))
    assert execute(cfg, gh, ds, dry_run=True) == [] and fake.dispatched() == []
    errors = execute(cfg, gh, ds)
    assert len(errors) == 1 and "HTTP 403" in errors[0]


def test_cancelled_run_before_event_end_alerts_without_redispatch(config_dir):
    cfg = load_config(config_dir)
    fake = FakeGh(runs=[{"displayTitle": KEY, "status": "completed", "conclusion": "cancelled"}])
    gh = Gh(run=fake)
    ds = decide(cfg, gh, START + timedelta(minutes=5), timedelta(hours=3))
    assert [d.action for d in ds] == ["run-ended"] and ds[0].alert and "cancelled" in ds[0].detail
    execute(cfg, gh, ds)
    assert fake.dispatched() == []
