import httpx
import pytest
import respx
from typer.testing import CliRunner

from zoomctl import cli
from zoomctl.doctor import FAIL, OK, WARN, check
from zoomctl.spec import load_config
from zoomctl.zoom import Credentials, ZoomClient

API = "https://api.zoom.test/v2"
OAUTH = "https://zoom.test/oauth/token"
HOST = "host-room-101@example.edu"


@pytest.fixture
def zoom():
    return ZoomClient(Credentials("a", "b", "c"), api_base=API, oauth_url=OAUTH, sleep=lambda s: None)


def token_ok():
    respx.post(OAUTH).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))


def statuses(findings):
    return [f.status for f in findings]


@respx.mock
def test_all_good(config_dir, zoom):
    token_ok()
    respx.get(f"{API}/users/{HOST}").mock(return_value=httpx.Response(200, json={"type": 2, "status": "active"}))
    respx.get(f"{API}/users/{HOST}/settings").mock(
        return_value=httpx.Response(200, json={"feature": {"webinar": True, "webinar_capacity": 500}}))
    respx.get(f"{API}/users/{HOST}/webinars").mock(return_value=httpx.Response(200, json={"webinars": []}))
    f = check(load_config(config_dir), zoom)
    assert statuses(f) == [OK, OK, OK]
    assert "500 attendees" in f[1].detail


@respx.mock
def test_bad_credentials_stop_early(config_dir, zoom):
    respx.post(OAUTH).mock(return_value=httpx.Response(400, json={"reason": "Invalid client_id or client_secret"}))
    f = check(load_config(config_dir), zoom)
    assert statuses(f) == [FAIL] and "Invalid client_id" in f[0].detail and "Activated" in f[0].detail


@respx.mock
def test_missing_scope_is_reported_with_zoom_message(config_dir, zoom):
    token_ok()
    respx.get(f"{API}/users/{HOST}").mock(return_value=httpx.Response(
        400, json={"code": 4711, "message": "Invalid access token, does not contain scopes:[user:read:user:admin]"}))
    f = check(load_config(config_dir), zoom)
    assert f[1].status == FAIL and "user:read:user:admin" in f[1].detail and "4711" in f[1].detail


@respx.mock
def test_unlicensed_and_no_webinar_feature(config_dir, zoom):
    token_ok()
    respx.get(f"{API}/users/{HOST}").mock(return_value=httpx.Response(200, json={"type": 1, "status": "active"}))
    assert check(load_config(config_dir), zoom)[1].detail.startswith("user is Basic")


@respx.mock
def test_no_webinar_license(config_dir, zoom):
    token_ok()
    respx.get(f"{API}/users/{HOST}").mock(return_value=httpx.Response(200, json={"type": 2}))
    respx.get(f"{API}/users/{HOST}/settings").mock(return_value=httpx.Response(200, json={"feature": {"webinar": False}}))
    f = check(load_config(config_dir), zoom)
    assert statuses(f) == [OK, FAIL] and "Webinar license" in f[1].detail


@respx.mock
def test_settings_unreadable_warns_and_webinar_list_forbidden_fails(config_dir, zoom):
    token_ok()
    respx.get(f"{API}/users/{HOST}").mock(return_value=httpx.Response(200, json={"type": 2}))
    respx.get(f"{API}/users/{HOST}/settings").mock(return_value=httpx.Response(403, json={"message": "no"}))
    respx.get(f"{API}/users/{HOST}/webinars").mock(return_value=httpx.Response(403, json={"code": 4711,
                                                                                           "message": "scopes"}))
    assert statuses(check(load_config(config_dir), zoom)) == [OK, WARN, FAIL]


def test_cli_check_exit_codes_and_alert(config_dir, monkeypatch):
    from zoomctl import doctor

    monkeypatch.setattr(cli, "_zoom", lambda: None)
    monkeypatch.setattr(doctor, "check", lambda cfg, z: [doctor.Finding(OK, "credentials", "fine")])
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["check", "-c", str(config_dir)]).exit_code == 0
    r = runner.invoke(cli.app, ["check", "-c", str(config_dir), "--test-alert"])
    assert r.exit_code == 1 and "ALERT_WEBHOOK_URL is not set" in r.output
    monkeypatch.setattr(doctor, "check", lambda cfg, z: [doctor.Finding(FAIL, "credentials", "bad")])
    assert runner.invoke(cli.app, ["check", "-c", str(config_dir)]).exit_code == 1


@respx.mock
def test_cli_test_alert_posts_to_webhook(config_dir, monkeypatch):
    from zoomctl import doctor

    monkeypatch.setattr(cli, "_zoom", lambda: None)
    monkeypatch.setattr(doctor, "check", lambda cfg, z: [])
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.test/abc")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    hook = respx.post("https://hooks.test/abc").mock(return_value=httpx.Response(200))
    r = CliRunner().invoke(cli.app, ["check", "-c", str(config_dir), "--test-alert"])
    assert r.exit_code == 0 and hook.call_count == 1
    assert b"test alert" in hook.calls[0].request.content
