import json

import pytest
from typer.testing import CliRunner

from conftest import FakeZoom
from zoomctl import cli

runner = CliRunner()


@pytest.fixture
def zoom(monkeypatch):
    z = FakeZoom()
    monkeypatch.setattr(cli, "_zoom", lambda: z)
    return z


def invoke(*args):
    return runner.invoke(cli.app, list(args))


def test_validate_ok_and_failure(config_dir):
    ok = invoke("validate", "-c", str(config_dir))
    assert ok.exit_code == 0 and "1 room(s), 1 event(s) valid" in ok.output
    (config_dir / "rooms" / "room-101.yaml").write_text("apiVersion: nope\n")
    bad = invoke("validate", "-c", str(config_dir))
    assert bad.exit_code == 1


def test_plan_apply_plan(config_dir, zoom, tmp_path):
    md = tmp_path / "plan.md"
    r = invoke("plan", "-c", str(config_dir), "--now", "2026-09-01T00:00:00+00:00", "--markdown", str(md))
    assert r.exit_code == 0 and "1 change(s) pending" in r.output and "create" in md.read_text()
    assert zoom.mutations() == []
    r = invoke("apply", "-c", str(config_dir), "--now", "2026-09-01T00:00:00+00:00")
    assert r.exit_code == 0 and "✓ reconciled" in r.output and len(zoom.webinars) == 1
    r = invoke("plan", "-c", str(config_dir), "--now", "2026-09-01T00:00:00+00:00")
    assert "0 change(s) pending" in r.output


def test_apply_failure_exits_nonzero(config_dir, zoom, monkeypatch):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)

    def boom(user, body):
        raise RuntimeError("zoom down")

    zoom.create_webinar = boom
    r = invoke("apply", "-c", str(config_dir), "--now", "2026-09-01T00:00:00+00:00")
    assert r.exit_code == 1


def test_end(config_dir, zoom):
    invoke("apply", "-c", str(config_dir), "--now", "2026-09-01T00:00:00+00:00")
    r = invoke("end", "-c", str(config_dir), "-e", "cs101-guest-2026-09-10")
    assert r.exit_code == 0 and zoom.ended == list(zoom.webinars)


def test_schema_export_and_check(tmp_path):
    out = tmp_path / "schemas"
    assert invoke("schema", "--check", "--out", str(out)).exit_code == 1
    assert invoke("schema", "--out", str(out)).exit_code == 0
    assert invoke("schema", "--check", "--out", str(out)).exit_code == 0
    room = json.loads((out / "room.v1.json").read_text())
    assert room["title"] == "Room"


def test_repo_schemas_are_current():
    from pathlib import Path
    repo_schemas = Path(__file__).resolve().parents[2] / "schemas"
    assert invoke("schema", "--check", "--out", str(repo_schemas)).exit_code == 0, "run `zoomctl schema`"


def test_run_event_refuses_wrong_room(config_dir, zoom):
    r = invoke("node", "run-event", "-c", str(config_dir), "-e", "cs101-guest-2026-09-10",
               "--expect-room", "room-202")
    assert r.exit_code == 1


def test_run_event_requires_roomagent(config_dir, zoom, monkeypatch):
    monkeypatch.setattr("zoomctl.node.agent.find_roomagent", lambda explicit=None: None)
    r = invoke("node", "run-event", "-c", str(config_dir), "-e", "cs101-guest-2026-09-10")
    assert r.exit_code == 1
