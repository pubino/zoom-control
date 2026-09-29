from datetime import datetime, timedelta, timezone

import pytest

from conftest import write_event
from zoomctl.spec import ConfigError, load_config, parse_marker, spec_hash, with_marker


def test_example_config_is_valid(config_dir):
    cfg = load_config(config_dir)
    assert set(cfg.rooms) == {"room-101"}
    ev = cfg.event("cs101-guest-2026-09-10")
    assert ev.start == datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc)
    assert ev.end - ev.start == timedelta(minutes=60)
    assert ev.run_key == "event-cs101-guest-2026-09-10-20260910T1800Z"


def test_naive_start_time_is_rejected(config_dir):
    (config_dir / "events" / "bad.yaml").write_text(
        "apiVersion: zoomcontrol/v1\nkind: ZoomWebinar\nmetadata: {uid: bad-one, name: x}\n"
        "spec: {room: nowhere, start_time: '2026-09-10T10:00:00', duration_minutes: 30}\n")
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any("start_time" in p and "UTC offset" in p for p in exc.value.problems)


def test_unknown_room_reference(config_dir):
    write_event(config_dir, "orphan", datetime(2026, 10, 1, 15, tzinfo=timezone.utc), room="room-999")
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert exc.value.problems == ["event 'orphan': references unknown room 'room-999'"]


def test_overlap_including_grace_and_preflight_is_rejected(config_dir):
    # example ends 19:00Z + 10 grace; this one's preflight begins 19:05Z -> overlap
    write_event(config_dir, "too-close", datetime(2026, 9, 10, 19, 20, tzinfo=timezone.utc))
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert "overlap" in exc.value.problems[0]


def test_extra_fields_are_forbidden(config_dir):
    p = config_dir / "rooms" / "room-101.yaml"
    p.write_text(p.read_text() + "bogus: 1\n")
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert any("bogus" in x for x in exc.value.problems)


def test_marker_roundtrip_and_hash_sensitivity(config_dir):
    cfg = load_config(config_dir)
    ev = cfg.event("cs101-guest-2026-09-10")
    room = cfg.room_for(ev)
    body = with_marker(ev, room)
    assert body["type"] == 5 and body["start_time"] == "2026-09-10T18:00:00Z"
    assert body["settings"]["auto_recording"] == "cloud"
    assert parse_marker(body["agenda"]) == (ev.uid, spec_hash(ev, room))
    changed = ev.model_copy(update={"spec": ev.spec.model_copy(update={"duration_minutes": 90})})
    assert spec_hash(changed, room) != spec_hash(ev, room)
    assert parse_marker("no marker here") is None
    assert parse_marker(None) is None


def _room(config_dir, rid, host):
    (config_dir / "rooms" / f"{rid}.yaml").write_text(
        (config_dir / "rooms" / "room-101.yaml").read_text()
        .replace("id: room-101", f"id: {rid}").replace("runner_label: room-101", f"runner_label: {rid}")
        .replace("host-room-101@example.edu", host))


def test_same_host_cannot_run_two_rooms_at_once(config_dir):
    _room(config_dir, "room-102", "host-room-101@example.edu")
    write_event(config_dir, "parallel", datetime(2026, 9, 10, 18, 30, tzinfo=timezone.utc), room="room-102")
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    assert exc.value.problems == ["host 'host-room-101@example.edu': events 'cs101-guest-2026-09-10' and "
                                  "'parallel' overlap (including grace and preflight windows)"]


def test_different_hosts_may_overlap_across_rooms(config_dir):
    _room(config_dir, "room-102", "other-host@example.edu")
    write_event(config_dir, "parallel", datetime(2026, 9, 10, 18, 30, tzinfo=timezone.utc), room="room-102")
    assert len(load_config(config_dir).events) == 2


def test_zoom_host_must_be_email_and_is_normalized(config_dir):
    p = config_dir / "rooms" / "room-101.yaml"
    p.write_text(p.read_text().replace("host-room-101@example.edu", "Host-Room-101@Example.edu"))
    assert load_config(config_dir).rooms["room-101"].spec.zoom_host == "host-room-101@example.edu"
    p.write_text(p.read_text().replace("Host-Room-101@Example.edu", "orfetalks"))
    with pytest.raises(ConfigError, match="email"):
        load_config(config_dir)
