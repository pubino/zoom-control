import os
import stat
import textwrap

import pytest

from zoomctl.node.agent import Report, RoomAgentCLI, find_roomagent, room_args
from zoomctl.spec import load_config


def fake_binary(tmp_path, body):
    p = tmp_path / "roomagent"
    p.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_parse_valid_report():
    r = Report.parse("health", '{"command":"health","status":"warn","checks":[{"name":"a","status":"warn",'
                               '"detail":"low"}]}')
    assert r.ok and r.status == "warn" and r.summary() == "a=warn(low)"


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"status":"great"}'])
def test_silence_or_garbage_is_failure(raw):
    r = Report.parse("health", raw)
    assert r.status == "fail" and not r.ok and r.error


def test_cli_passes_room_args_and_parses(tmp_path, config_dir):
    room = load_config(config_dir).rooms["room-101"]
    log = tmp_path / "args"
    binary = fake_binary(tmp_path, f"""
        echo "$@" > {log}
        echo '{{"command":"'$1'","status":"ok","checks":[]}}'
    """)
    r = RoomAgentCLI(binary).preflight(room)
    assert r.status == "ok"
    assert log.read_text().split()[0] == "preflight"
    assert "--video-device" in room_args(room) and "Display 2" in room_args(room)


def test_start_url_goes_via_stdin_not_argv(tmp_path):
    log = tmp_path / "log"
    binary = fake_binary(tmp_path, f"""
        echo "argv: $@" > {log}
        read url; echo "stdin: $url" >> {log}
        echo '{{"command":"launch","status":"ok","checks":[]}}'
    """)
    assert RoomAgentCLI(binary).launch("https://zoom.us/s/1?zak=SECRET").ok
    text = log.read_text()
    assert "SECRET" not in text.splitlines()[0] and "stdin: https://zoom.us/s/1?zak=SECRET" in text


def test_crash_with_no_output_is_failure(tmp_path):
    binary = fake_binary(tmp_path, "echo 'segfault' >&2; exit 139\n")
    r = RoomAgentCLI(binary).quit()
    assert r.status == "fail" and "invalid JSON" in r.error


def test_nonzero_exit_overrides_ok_json(tmp_path):
    binary = fake_binary(tmp_path, "echo '{\"status\":\"ok\"}'; echo oops >&2; exit 5\n")
    r = RoomAgentCLI(binary).quit()
    assert r.status == "fail" and "exit 5" in r.error


def test_timeout_is_failure(tmp_path):
    binary = fake_binary(tmp_path, "sleep 5\n")
    r = RoomAgentCLI(binary, timeout=0.2).quit()
    assert r.status == "fail" and "timed out" in r.error


def test_missing_binary_is_failure(tmp_path):
    r = RoomAgentCLI(str(tmp_path / "nope")).quit()
    assert r.status == "fail" and "could not start" in r.error


def test_find_roomagent_prefers_explicit(tmp_path, monkeypatch):
    binary = fake_binary(tmp_path, "exit 0\n")
    monkeypatch.setenv("ROOMAGENT", "/definitely/missing")
    assert find_roomagent(binary) == binary


def test_find_roomagent_uses_env_then_none(tmp_path, monkeypatch):
    binary = fake_binary(tmp_path, "exit 0\n")
    monkeypatch.setenv("ROOMAGENT", binary)
    assert find_roomagent(None) == binary
    monkeypatch.setenv("ROOMAGENT", str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr("os.path.isfile", lambda p: False)
    assert find_roomagent(None) is None
