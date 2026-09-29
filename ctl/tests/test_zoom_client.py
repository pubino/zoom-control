import httpx
import pytest
import respx

from zoomctl.zoom import ZoomAuthError, ZoomClient, ZoomError

API = "https://api.zoom.test/v2"
HOST = "orfetalks@princeton.edu"


class Tokens:
    """Scripted provider: hands out tokens in order; records `rejected` hints."""

    host = HOST

    def __init__(self, *tokens):
        self.tokens = list(tokens) or ["t1"]
        self.rejected = []

    def access_token(self, rejected=None):
        if rejected:
            self.rejected.append(rejected)
            self.tokens.pop(0)
        return self.tokens[0]


def make(tokens=None, verified=True, **kw):
    sleeps: list[float] = []
    client = ZoomClient(tokens or Tokens(), api_base=API, sleep=sleeps.append, **kw)
    client._verified = verified
    return client, sleeps


def me_ok(email=HOST):
    return respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, json={"email": email, "type": 2}))


@respx.mock
def test_bearer_token_sent():
    route = respx.get(f"{API}/webinars/1").mock(return_value=httpx.Response(200, json={"id": 1}))
    client, _ = make()
    assert client.get_webinar(1) == {"id": 1}
    assert route.calls[0].request.headers["Authorization"] == "Bearer t1"


@respx.mock
def test_401_retries_once_with_rejected_hint():
    tokens = Tokens("old", "new")
    route = respx.get(f"{API}/webinars/1").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"id": 1})])
    client, _ = make(tokens)
    assert client.get_webinar(1)["id"] == 1
    assert tokens.rejected == ["old"]
    assert route.calls[1].request.headers["Authorization"] == "Bearer new"


@respx.mock
def test_second_401_is_an_error():
    respx.get(f"{API}/webinars/1").mock(return_value=httpx.Response(401, json={"code": 124}))
    client, _ = make(Tokens("a", "b"))
    with pytest.raises(ZoomError) as exc:
        client.get_webinar(1)
    assert exc.value.status == 401


@respx.mock
def test_429_backs_off_honouring_retry_after():
    respx.get(f"{API}/webinars/1").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(503), httpx.Response(200, json={"id": 1})])
    client, sleeps = make()
    assert client.get_webinar(1)["id"] == 1
    assert sleeps == [7.0, 2.0]


@respx.mock
def test_retries_exhausted_raises_with_status():
    respx.get(f"{API}/webinars/1").mock(return_value=httpx.Response(500, json={"message": "boom"}))
    client, sleeps = make(max_retries=2)
    with pytest.raises(ZoomError) as exc:
        client.get_webinar(1)
    assert exc.value.status == 500 and len(sleeps) == 2


@respx.mock
def test_4xx_is_not_retried():
    route = respx.delete(f"{API}/webinars/9").mock(return_value=httpx.Response(404, json={"code": 3001}))
    client, sleeps = make()
    with pytest.raises(ZoomError) as exc:
        client.delete_webinar(9)
    assert exc.value.status == 404 and route.call_count == 1 and sleeps == []


@respx.mock
def test_only_own_resources_are_addressed():
    me_ok()
    lst = respx.get(f"{API}/users/me/webinars").mock(side_effect=[
        httpx.Response(200, json={"webinars": [{"id": 1}], "next_page_token": "p2"}),
        httpx.Response(200, json={"webinars": [{"id": 2}], "next_page_token": ""}),
    ])
    create = respx.post(f"{API}/users/me/webinars").mock(return_value=httpx.Response(201, json={"id": 3}))
    client, _ = make(verified=False)
    assert [w["id"] for w in client.list_webinars()] == [1, 2]
    assert lst.calls[1].request.url.params["next_page_token"] == "p2"
    assert client.create_webinar({"topic": "x"})["id"] == 3
    assert create.called


@respx.mock
def test_identity_mismatch_blocks_every_operation():
    me_ok("someone-else@princeton.edu")
    delete = respx.delete(f"{API}/webinars/5").mock(return_value=httpx.Response(204))
    client, _ = make(verified=False)
    for op in (lambda: list(client.list_webinars()), lambda: client.create_webinar({}),
               lambda: client.delete_webinar(5), lambda: client.end_webinar(5), lambda: client.get_webinar(5)):
        with pytest.raises(ZoomAuthError, match="not orfetalks@princeton.edu"):
            op()
    assert not delete.called


@respx.mock
def test_identity_is_verified_once():
    me = me_ok()
    respx.get(f"{API}/webinars/5").mock(return_value=httpx.Response(200, json={"start_url": "https://zoom.us/s/5?zak=A"}))
    client, _ = make(verified=False)
    client.get_webinar(5)
    client.get_webinar(5)
    assert me.call_count == 1


@respx.mock
def test_end_uses_webinar_status_endpoint_and_fresh_start_url():
    end = respx.put(f"{API}/webinars/5/status").mock(return_value=httpx.Response(204))
    respx.get(f"{API}/webinars/5").mock(side_effect=[
        httpx.Response(200, json={"start_url": "https://zoom.us/s/5?zak=A"}),
        httpx.Response(200, json={"start_url": "https://zoom.us/s/5?zak=B"}),
        httpx.Response(200, json={}),
    ])
    client, _ = make()
    client.end_webinar(5)
    assert end.calls[0].request.content == b'{"action":"end"}'
    assert client.fresh_start_url(5).endswith("zak=A")
    assert client.fresh_start_url(5).endswith("zak=B")
    with pytest.raises(ZoomError, match="no start_url"):
        client.fresh_start_url(5)
