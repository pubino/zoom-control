import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from zoomctl.auth import complete_login, is_loopback, logout, parse_callback, wait_for_loopback
from zoomctl.zoom.errors import ZoomAuthError
from zoomctl.zoom.tokens import REVOKE_URL, TOKEN_URL, FileTokenStore, OAuthApp

HOST = "orfetalks@princeton.edu"
API = "https://api.zoom.us/v2"
REDIRECT = "http://127.0.0.1:8765/zoom/callback"


@pytest.fixture
def store(tmp_path):
    return FileTokenStore(tmp_path / "tokens")


def test_begin_uses_pkce_s256_and_state():
    url, state, verifier = OAuthApp("cid", None, REDIRECT).begin()
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["state"] == [state] and q["redirect_uri"] == [REDIRECT]
    assert 43 <= len(verifier) <= 128 and q["code_challenge"][0] != verifier


def test_parse_callback():
    assert parse_callback(f"{REDIRECT}?code=abc&state=s1", "s1") == "abc"
    with pytest.raises(ZoomAuthError, match="state mismatch"):
        parse_callback(f"{REDIRECT}?code=abc&state=other", "s1")
    with pytest.raises(ZoomAuthError, match="denied"):
        parse_callback(f"{REDIRECT}?error=access_denied&state=s1", "s1")
    with pytest.raises(ZoomAuthError, match="no \\?code"):
        parse_callback(f"{REDIRECT}?state=s1", "s1")


def test_is_loopback():
    assert is_loopback(REDIRECT)
    assert not is_loopback("http://localhost:8765/cb")  # Zoom rejects localhost; use 127.0.0.1
    assert not is_loopback("https://example.edu/cb")


def token_and_me(email=HOST, refresh="rt1"):
    tok = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "at1", "refresh_token": refresh, "expires_in": 3599, "scope": "webinar:read:webinar"}))
    me = respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json={"email": email, "id": "u1"}))
    return tok, me


@respx.mock
def test_complete_login_verifies_identity_then_stores(store):
    tok, _ = token_and_me(email="OrfeTalks@Princeton.edu")
    rec, user = complete_login(OAuthApp("cid", "sec", REDIRECT), HOST, "code1", "ver", store)
    body = tok.calls[0].request.content.decode()
    assert "grant_type=authorization_code" in body and "code_verifier=ver" in body
    saved = store.load(HOST)
    assert saved.refresh_token == "rt1" and saved.zoom_user_id == "u1" and saved.client_secret == "sec"


@respx.mock
def test_wrong_account_is_revoked_and_not_stored(store):
    token_and_me(email="bino@princeton.edu")
    revoke = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    with pytest.raises(ZoomAuthError, match="signed in as bino@princeton.edu"):
        complete_login(OAuthApp("cid", "sec", REDIRECT), HOST, "c", "v", store)
    assert revoke.called and store.load(HOST) is None


@respx.mock
def test_missing_refresh_token_is_rejected(store):
    token_and_me(refresh="")
    with pytest.raises(ZoomAuthError, match="no refresh_token"):
        complete_login(OAuthApp("cid", "sec", REDIRECT), HOST, "c", "v", store)
    assert store.load(HOST) is None


@respx.mock
def test_logout_revokes_and_deletes(store):
    token_and_me()
    complete_login(OAuthApp("cid", "sec", REDIRECT), HOST, "c", "v", store)
    revoke = respx.post(REVOKE_URL).mock(return_value=httpx.Response(200))
    assert logout(HOST, store) is True
    assert "token=rt1" in revoke.calls[0].request.content.decode() and store.load(HOST) is None
    assert logout(HOST, store) is False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_wait_for_loopback_captures_callback():
    uri = f"http://127.0.0.1:{free_port()}/zoom/callback"
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("url", wait_for_loopback(uri, timeout=5)))
    t.start()
    for _ in range(50):
        try:
            assert httpx.get(uri.replace("/zoom/callback", "/other")).status_code == 404
            r = httpx.get(f"{uri}?code=abc&state=s")
            break
        except httpx.ConnectError:
            time.sleep(0.05)
    t.join(5)
    assert r.status_code == 200 and parse_callback(result["url"], "s") == "abc"


def test_wait_for_loopback_times_out():
    with pytest.raises(ZoomAuthError, match="no redirect"):
        wait_for_loopback(f"http://127.0.0.1:{free_port()}/cb", timeout=1)
