import json
import stat
import subprocess
import threading
import time

import httpx
import pytest
import respx

from zoomctl.zoom.errors import ZoomAuthError
from zoomctl.zoom.tokens import (TOKEN_URL, FileTokenStore, KeychainTokenStore, TokenRecord, UserTokenProvider,
                                 default_store)

HOST = "orfetalks@princeton.edu"


def record(**kw):
    base = dict(host=HOST, client_id="cid", client_secret="sec", refresh_token="rt0", access_token="",
                expires_at=0.0, refreshed_at=1000.0)
    base.update(kw)
    return TokenRecord(**base)


@pytest.fixture
def store(tmp_path):
    return FileTokenStore(tmp_path / "tokens")


def test_file_store_roundtrip_permissions_and_hosts(store):
    assert store.load(HOST) is None and store.hosts() == []
    store.save(record())
    assert store.load("ORFETALKS@princeton.edu").refresh_token == "rt0"
    p = store.root / f"{HOST}.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert store.hosts() == [HOST]
    store.delete(HOST)
    assert store.load(HOST) is None


def test_record_tolerates_unknown_fields():
    raw = json.dumps({**json.loads(record().to_json()), "future_field": 1})
    assert TokenRecord.from_json(raw).host == HOST


@respx.mock
def test_refresh_rotates_and_persists_before_returning(store):
    store.save(record())
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "at1", "refresh_token": "rt1", "expires_in": 3599, "scope": "webinar:read:webinar"}))
    p = UserTokenProvider(HOST, store, clock=lambda: 5000.0)
    assert p.access_token() == "at1"
    saved = store.load(HOST)
    assert (saved.refresh_token, saved.access_token, saved.refreshed_at) == ("rt1", "at1", 5000.0)
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=rt0" in body
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")
    # cached: no second refresh while valid
    assert p.access_token() == "at1" and route.call_count == 1


@respx.mock
def test_public_client_sends_client_id_without_basic_auth(store):
    store.save(record(client_secret=None))
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "a", "refresh_token": "r"}))
    UserTokenProvider(HOST, store).access_token()
    req = route.calls[0].request
    assert "client_id=cid" in req.content.decode() and "Authorization" not in req.headers


@respx.mock
def test_valid_stored_token_is_used_without_refresh(store):
    store.save(record(access_token="shared", expires_at=time.time() + 3000))
    route = respx.post(TOKEN_URL)
    assert UserTokenProvider(HOST, store).access_token() == "shared" and not route.called


@respx.mock
def test_rejected_token_forces_refresh_unless_another_process_already_did(store):
    store.save(record(access_token="bad", expires_at=time.time() + 3000))
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "fresh",
                                                                              "refresh_token": "rt1"}))
    p = UserTokenProvider(HOST, store)
    assert p.access_token() == "bad"
    # another process rotated meanwhile:
    store.save(record(access_token="other", expires_at=time.time() + 3000, refresh_token="rtX"))
    assert p.access_token(rejected="bad") == "other" and not route.called
    assert p.access_token(rejected="other") == "fresh" and route.call_count == 1


@respx.mock
def test_invalid_grant_explains_reauthorization(store):
    store.save(record())
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"reason": "Invalid Token!",
                                                                      "error": "invalid_grant"}))
    with pytest.raises(ZoomAuthError, match="zoomctl auth login --host orfetalks@princeton.edu"):
        UserTokenProvider(HOST, store).access_token()
    assert store.load(HOST).refresh_token == "rt0"  # never overwritten by a failure


def test_missing_and_foreign_records(store):
    with pytest.raises(ZoomAuthError, match="no Zoom authorization"):
        UserTokenProvider(HOST, store).access_token()
    (store.root).mkdir(parents=True, exist_ok=True)
    (store.root / f"{HOST}.json").write_text(record(host="evil@princeton.edu").to_json())
    with pytest.raises(ZoomAuthError, match="belongs to evil"):
        UserTokenProvider(HOST, store).access_token()


@respx.mock
def test_concurrent_refreshes_are_serialized(tmp_path):
    root = tmp_path / "tokens"
    FileTokenStore(root).save(record())
    counter = {"n": 0}

    def rotate(request):
        counter["n"] += 1
        time.sleep(0.05)
        return httpx.Response(200, json={"access_token": f"at{counter['n']}", "refresh_token": f"rt{counter['n']}",
                                         "expires_in": 3600})

    respx.post(TOKEN_URL).mock(side_effect=rotate)
    results = []
    threads = [threading.Thread(target=lambda: results.append(UserTokenProvider(HOST, FileTokenStore(root))
                                                              .access_token())) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert counter["n"] == 1, "only one process may spend the single-use refresh token"
    assert results == ["at1"] * 5


class FakeSecurity:
    def __init__(self):
        self.items = {}
        self.calls = []

    def __call__(self, args, capture_output, text):
        self.calls.append(args)
        cmd = args[1]
        acct = args[args.index("-a") + 1]
        if cmd == "find-generic-password":
            if acct not in self.items:
                return subprocess.CompletedProcess(args, 44, "", "could not be found")
            return subprocess.CompletedProcess(args, 0, self.items[acct] + "\n", "")
        if cmd == "add-generic-password":
            self.items[acct] = args[args.index("-w") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd == "delete-generic-password":
            self.items.pop(acct, None)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


def test_keychain_store(tmp_path):
    sec = FakeSecurity()
    ks = KeychainTokenStore(state_dir=tmp_path, run=sec)
    assert ks.load(HOST) is None
    ks.save(record())
    add = next(c for c in sec.calls if c[1] == "add-generic-password")
    assert add[0] == "/usr/bin/security" and "-U" in add and add[add.index("-T") + 1] == "/usr/bin/security"
    assert ks.load(HOST).refresh_token == "rt0" and ks.hosts() == [HOST]
    ks.delete(HOST)
    assert ks.hosts() == [] and ks.load(HOST) is None


def test_keychain_locked_is_an_error(tmp_path):
    def locked(args, capture_output, text):
        return subprocess.CompletedProcess(args, 51, "", "User interaction is not allowed.")

    with pytest.raises(ZoomAuthError, match="unlocked"):
        KeychainTokenStore(state_dir=tmp_path, run=locked).load(HOST)


def test_default_store_selection(tmp_path, monkeypatch):
    assert isinstance(default_store({"ZOOMCTL_TOKEN_STORE": f"file:{tmp_path}"}), FileTokenStore)
    assert isinstance(default_store({"ZOOMCTL_TOKEN_STORE": "keychain"}), KeychainTokenStore)
    with pytest.raises(ZoomAuthError):
        default_store({"ZOOMCTL_TOKEN_STORE": "vault"})
    monkeypatch.setattr("sys.platform", "linux")
    assert isinstance(default_store({"XDG_CONFIG_HOME": str(tmp_path)}), FileTokenStore)
