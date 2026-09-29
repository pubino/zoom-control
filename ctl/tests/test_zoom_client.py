import httpx
import pytest
import respx

from zoomctl.zoom import Credentials, ZoomClient, ZoomError

API = "https://api.zoom.test/v2"
OAUTH = "https://zoom.test/oauth/token"
CREDS = Credentials("acct", "cid", "secret")


def make(**kw):
    sleeps: list[float] = []
    client = ZoomClient(CREDS, api_base=API, oauth_url=OAUTH, sleep=sleeps.append, **kw)
    return client, sleeps


def token_route(mock, *tokens):
    return mock.post(OAUTH).mock(side_effect=[httpx.Response(200, json={"access_token": t, "expires_in": 3600})
                                              for t in tokens])


@respx.mock
def test_token_is_cached_and_sent():
    tok = token_route(respx, "t1")
    route = respx.get(f"{API}/webinars/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    client, _ = make()
    assert client.get_webinar(1) == {"id": 1}
    assert client.get_webinar(1) == {"id": 1}
    assert tok.call_count == 1
    assert route.calls[0].request.headers["Authorization"] == "Bearer t1"
    req = tok.calls[0].request
    assert req.url.params["grant_type"] == "account_credentials" and req.url.params["account_id"] == "acct"
    assert req.headers["Authorization"].startswith("Basic ")


@respx.mock
def test_401_triggers_single_reauth():
    token_route(respx, "old", "new")
    route = respx.get(f"{API}/webinars/1").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"id": 1})])
    client, _ = make()
    assert client.get_webinar(1)["id"] == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer new"


@respx.mock
def test_429_backs_off_honouring_retry_after():
    token_route(respx, "t")
    respx.get(f"{API}/webinars/1").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(503), httpx.Response(200, json={"id": 1})])
    client, sleeps = make()
    assert client.get_webinar(1)["id"] == 1
    assert sleeps == [7.0, 2.0]


@respx.mock
def test_retries_exhausted_raises_with_status():
    token_route(respx, "t")
    respx.get(f"{API}/webinars/1").mock(return_value=httpx.Response(500, json={"message": "boom"}))
    client, sleeps = make(max_retries=2)
    with pytest.raises(ZoomError) as exc:
        client.get_webinar(1)
    assert exc.value.status == 500 and len(sleeps) == 2


@respx.mock
def test_4xx_is_not_retried():
    token_route(respx, "t")
    route = respx.delete(f"{API}/webinars/9").mock(return_value=httpx.Response(404, json={"code": 3001}))
    client, sleeps = make()
    with pytest.raises(ZoomError) as exc:
        client.delete_webinar(9)
    assert exc.value.status == 404 and route.call_count == 1 and sleeps == []


@respx.mock
def test_bad_credentials_fail_loudly():
    respx.post(OAUTH).mock(return_value=httpx.Response(400, json={"reason": "Invalid client_id"}))
    client, _ = make()
    with pytest.raises(ZoomError, match="OAuth token request failed"):
        client.get_webinar(1)


@respx.mock
def test_list_webinars_paginates():
    token_route(respx, "t")
    respx.get(f"{API}/users/h@x.edu/webinars").mock(side_effect=[
        httpx.Response(200, json={"webinars": [{"id": 1}], "next_page_token": "p2"}),
        httpx.Response(200, json={"webinars": [{"id": 2}], "next_page_token": ""}),
    ])
    client, _ = make()
    assert [w["id"] for w in client.list_webinars("h@x.edu")] == [1, 2]


@respx.mock
def test_end_uses_webinar_status_endpoint_and_fresh_start_url():
    token_route(respx, "t")
    end = respx.put(f"{API}/webinars/5/status").mock(return_value=httpx.Response(204))
    respx.get(f"{API}/webinars/5").mock(side_effect=[
        httpx.Response(200, json={"start_url": "https://zoom.us/s/5?zak=A"}),
        httpx.Response(200, json={"start_url": "https://zoom.us/s/5?zak=B"}),
        httpx.Response(200, json={}),
    ])
    client, _ = make()
    client.end_webinar(5)
    assert end.calls[0].request.content == b'{"action":"end"}'
    # expired-ZAK scenario: each fetch returns the current URL, never a cached one
    assert client.fresh_start_url(5).endswith("zak=A")
    assert client.fresh_start_url(5).endswith("zak=B")
    with pytest.raises(ZoomError, match="no start_url"):
        client.fresh_start_url(5)


def test_missing_env_credentials_listed():
    with pytest.raises(ZoomError, match="ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET"):
        Credentials.from_env({"ZOOM_ACCOUNT_ID": "a"})
