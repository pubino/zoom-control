from datetime import datetime, timedelta, timezone

from conftest import NOW, write_event
from zoomctl.reconcile import Action, apply, find_webinar_id, plan
from zoomctl.spec import load_config

import pytest

HOST = "host-room-101@example.edu"
UID = "cs101-guest-2026-09-10"


def actions(changes):
    return sorted((c.action.value, c.uid) for c in changes)


def edit_example(config_dir, old, new):
    p = config_dir / "events" / "2026-09-10-cs101-guest.yaml"
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new))


def test_create_then_noop_is_idempotent(config_dir, fake_zoom):
    cfg = load_config(config_dir)
    first = plan(cfg, fake_zoom.zoom_for, now=NOW)
    assert actions(first) == [("create", UID)]
    assert apply(first, fake_zoom.zoom_for) == []
    assert first[0].webinar_id in fake_zoom.webinars
    second = plan(cfg, fake_zoom.zoom_for, now=NOW)
    assert actions(second) == [("noop", UID)]
    assert apply(second, fake_zoom.zoom_for) == []
    assert [c[0] for c in fake_zoom.mutations()] == ["create"]


def test_spec_change_patches_existing(config_dir, fake_zoom):
    apply(plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW), fake_zoom.zoom_for)
    edit_example(config_dir, "duration_minutes: 60", "duration_minutes: 90")
    ch = plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW)
    assert actions(ch) == [("update", UID)]
    assert apply(ch, fake_zoom.zoom_for) == []
    wid = ch[0].webinar_id
    assert fake_zoom.webinars[wid]["duration"] == 90
    assert actions(plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW)) == [("noop", UID)]


def test_removed_spec_prunes_future_but_keeps_past(config_dir, fake_zoom):
    future = fake_zoom.add(HOST, agenda="x [zc:gone-future:0123456789ab]", start_time="2026-12-01T15:00:00Z")
    past = fake_zoom.add(HOST, agenda="[zc:gone-past:0123456789ab]", start_time="2026-01-01T15:00:00Z")
    unmanaged = fake_zoom.add(HOST, agenda="someone else's webinar", start_time="2026-12-01T15:00:00Z")
    ch = plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW)
    assert actions(ch) == [("create", UID), ("delete", "gone-future"), ("skip", "gone-past")]
    apply(ch, fake_zoom.zoom_for)
    assert future not in fake_zoom.webinars
    assert past in fake_zoom.webinars and unmanaged in fake_zoom.webinars


def test_no_prune_keeps_orphans(config_dir, fake_zoom):
    fake_zoom.add(HOST, agenda="[zc:gone:0123456789ab]", start_time="2026-12-01T15:00:00Z")
    ch = plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW, prune=False)
    assert ("skip", "gone") in actions(ch)


def test_past_event_is_never_touched(config_dir, fake_zoom):
    ch = plan(load_config(config_dir), fake_zoom.zoom_for, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert actions(ch) == [("skip", UID)]


def test_duplicates_are_collapsed(config_dir, fake_zoom):
    cfg = load_config(config_dir)
    apply(plan(cfg, fake_zoom.zoom_for, now=NOW), fake_zoom.zoom_for)
    original = next(iter(fake_zoom.webinars.values()))
    dup = fake_zoom.add(HOST, agenda=original["agenda"], start_time=original["start_time"])
    ch = plan(cfg, fake_zoom.zoom_for, now=NOW)
    assert actions(ch) == [("delete", UID), ("noop", UID)]
    assert next(c for c in ch if c.action is Action.DELETE).webinar_id == dup


def test_list_without_agenda_falls_back_to_detail(config_dir, fake_zoom):
    cfg = load_config(config_dir)
    apply(plan(cfg, fake_zoom.zoom_for, now=NOW), fake_zoom.zoom_for)
    original_list = fake_zoom.list_webinars

    def list_no_agenda(user):
        for w in original_list(user):
            w.pop("agenda", None)
            yield w

    fake_zoom.list_webinars = list_no_agenda
    assert actions(plan(cfg, fake_zoom.zoom_for, now=NOW)) == [("noop", UID)]
    assert any(c[0] == "get" for c in fake_zoom.calls)


def test_apply_collects_errors(config_dir, fake_zoom):
    def boom(user, body):
        raise RuntimeError("zoom down")

    fake_zoom.create_webinar = boom
    errors = apply(plan(load_config(config_dir), fake_zoom.zoom_for, now=NOW), fake_zoom.zoom_for)
    assert len(errors) == 1 and "zoom down" in errors[0]


def test_find_webinar_id(config_dir, fake_zoom):
    cfg = load_config(config_dir)
    with pytest.raises(LookupError, match="zoomctl apply"):
        find_webinar_id(cfg, fake_zoom.zoom_for, cfg.event(UID))
    ch = plan(cfg, fake_zoom.zoom_for, now=NOW)
    apply(ch, fake_zoom.zoom_for)
    assert find_webinar_id(cfg, fake_zoom.zoom_for, cfg.event(UID)) == ch[0].webinar_id
