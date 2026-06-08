import httpx
import pytest
from amap_service.clients.base import HttpClient


def _client_with(handler, **kw):
    return HttpClient(timeout_seconds=5, backoff_seconds=0,
                      transport=httpx.MockTransport(handler), **kw)


def test_get_json_success():
    def handler(request):
        assert request.url.path == "/x"
        return httpx.Response(200, json={"linkCoordList": [], "n": 1})
    with _client_with(handler) as c:
        assert c.get_json("http://h/x") == {"linkCoordList": [], "n": 1}


def test_get_json_preserves_bigint():
    def handler(request):
        return httpx.Response(200, content=b'{"linkId": 5130091959790075998}',
                              headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        assert c.get_json("http://h/x")["linkId"] == 5130091959790075998


def test_retries_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})
    with _client_with(handler, max_retries=3) as c:
        assert c.get_json("http://h/x") == {"ok": True}
    assert calls["n"] == 3


def test_gives_up_and_raises():
    def handler(request):
        return httpx.Response(503)
    with _client_with(handler, max_retries=2) as c:
        with pytest.raises(httpx.HTTPError):
            c.get_json("http://h/x")


def test_passes_params_and_headers():
    seen = {}
    def handler(request):
        seen["q"] = request.url.params.get("loginname")
        seen["auth"] = request.headers.get("x-token")
        return httpx.Response(200, json={})
    with _client_with(handler, headers={"x-token": "T"}) as c:
        c.get_json("http://h/x", params={"loginname": "yangs"})
    assert seen == {"q": "yangs", "auth": "T"}


def test_stream_items_yields_each_element():
    payload = (b'{"linkCoordList":[{"linkId":5130091959790075998,"coordList":[120.9,31.0]},'
               b'{"linkId":5130091959790075999,"coordList":[1.0,2.0]}]}')
    def handler(request):
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        items = list(c.stream_items("http://h/areaLinkPub", "linkCoordList.item"))
    assert len(items) == 2
    assert items[0]["linkId"] == 5130091959790075998


def test_stream_items_raises_on_http_error():
    def handler(request):
        return httpx.Response(500)
    with _client_with(handler) as c:
        with pytest.raises(httpx.HTTPError):
            list(c.stream_items("http://h/x", "linkCoordList.item"))
