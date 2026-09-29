import time

import httpx
import pytest
import respx
from typer.testing import CliRunner

from zoomctl import cli
from zoomctl.doctor import FAIL, OK, WARN, check
from zoomctl.spec import load_config
from zoomctl.zoom import ZoomClient
from zoomctl.zoom.tokens import FileTokenStore, TokenRecord, UserTokenProvider

API = "https://api.zoom.test/v2"
HOST = "host-room-101@example.edu"


@pytest.fixture
def store(tmp_path):
    return FileTokenStore(tmp_path / "tokens")


def authorize(store, host=HOST, refreshed_days_ago=1):
    now = time.time()
    store.save(TokenRecord(host=host, client_id="c", client_secret="s", refresh_token="r", access_token="at",
                           expires_at=now + 3000, refreshed_at=now - refreshed_days_ago * 86400))


def zoom_for(store):
    return lambda host: ZoomClient(UserTokenProvider(host, store), api_base=API, sleep=lambda s: None)


def statuses(findings):
    return [f.status for f in findings]


def mock_user(email=HOST, type_=2, feature=None, list_status=200):
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json={"email": email, "type": type_}))
    respx.get(f"{API}/users/me/settings").mock(
        return_value=httpx.Response(200, json={"feature": feature if feature is not None else
                                                          {"webinar": True, "webinar_capacity": 500}}))
    respx.get(f"{API}/users/me/webinars").mock(return_value=httpx.Response(list_status, json={"webinars": [],
                                                                                              "code": 4711,
                                                                                              "message": "scopes"}))


@respx.mock
def test_all_good(config_dir, store):
    authorize(store)
    mock_user()
    f = check(load_config(config_dir), zoom_for(store), store)
    assert statuses(f) == [OK, OK, OK, OK], [x.line() for x in f]
    assert "500 attendees" in f[2].detail


def test_not_authorized_on_node(config_dir, store):
    f = check(load_config(config_dir), zoom_for(store), store)
    assert statuses(f) == [OK, FAIL] and "zoomctl auth login --host host-room-101@example.edu" in f[1].detail


@respx.mock
def test_token_for_wrong_user(config_dir, store):
    authorize(store)
    mock_user(email="bino@princeton.edu")
    f = check(load_config(config_dir), zoom_for(store), store)
    assert f[1].status == FAIL and "bino@princeton.edu" in f[1].detail


@respx.mock
def test_unlicensed_no_webinar_and_old_refresh_token(config_dir, store):
    authorize(store, refreshed_days_ago=75)
    mock_user(feature={"webinar": False})
    f = check(load_config(config_dir), zoom_for(store), store)
    assert statuses(f) == [OK, OK, WARN, FAIL]
    assert "75 days" in f[2].detail and "Webinar license" in f[3].detail


@respx.mock
def test_missing_webinar_scope_and_stray_host(config_dir, store):
    authorize(store)
    authorize(store, host="old-host@example.edu")
    mock_user(list_status=400)
    f = check(load_config(config_dir), zoom_for(store), store)
    assert f[3].status == FAIL and "4711" in f[3].detail
    assert f[-1].status == WARN and "old-host@example.edu" in f[-1].detail


def test_cli_check_exit_codes_and_alert(config_dir, monkeypatch, store):
    from zoomctl import doctor

    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(doctor, "check", lambda cfg, z, s: [doctor.Finding(OK, "x", "fine")])
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["check", "-c", str(config_dir)]).exit_code == 0
    r = runner.invoke(cli.app, ["check", "-c", str(config_dir), "--test-alert"])
    assert r.exit_code == 1 and "ALERT_WEBHOOK_URL is not set" in r.output
    monkeypatch.setattr(doctor, "check", lambda cfg, z, s: [doctor.Finding(FAIL, "x", "bad")])
    assert runner.invoke(cli.app, ["check", "-c", str(config_dir)]).exit_code == 1


@respx.mock
def test_cli_test_alert_posts_to_webhook(config_dir, monkeypatch, store):
    from zoomctl import doctor

    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(doctor, "check", lambda cfg, z, s: [])
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.test/abc")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    hook = respx.post("https://hooks.test/abc").mock(return_value=httpx.Response(200))
    r = CliRunner().invoke(cli.app, ["check", "-c", str(config_dir), "--test-alert"])
    assert r.exit_code == 0 and hook.call_count == 1


def test_cli_auth_status(monkeypatch, store):
    monkeypatch.setattr(cli, "_store", lambda: store)
    r = CliRunner().invoke(cli.app, ["auth", "status"])
    assert r.exit_code == 1 and "no hosts authorized" in r.output
    authorize(store)
    r = CliRunner().invoke(cli.app, ["auth", "status"])
    assert r.exit_code == 0 and HOST in r.output and "access token valid" in r.output
