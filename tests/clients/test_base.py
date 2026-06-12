import gzip
import json

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


# 上游服务器返回 gzip body 但不带 Content-Encoding 头(httpx 因此不会自动解压)。
def test_get_json_decompresses_gzip_without_content_encoding():
    gz = gzip.compress(b'{"linkStates":[{"linkId":5130091959790075998,"state":2}]}')
    def handler(request):
        return httpx.Response(200, content=gz, headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        body = c.get_json("http://h/traffic")
    assert body["linkStates"][0]["state"] == 2
    assert body["linkStates"][0]["linkId"] == 5130091959790075998  # 64 位精度不丢


def test_get_json_labeled_gzip_not_double_decompressed():
    # 规范情形:带 Content-Encoding: gzip,httpx 已解压;不应再被二次解压。
    gz = gzip.compress(b'{"ok": true}')
    def handler(request):
        return httpx.Response(200, content=gz,
                              headers={"content-encoding": "gzip", "content-type": "application/json"})
    with _client_with(handler) as c:
        assert c.get_json("http://h/x") == {"ok": True}


def test_stream_items_decompresses_gzip_without_content_encoding():
    gz = gzip.compress(b'{"linkCoordList":[{"linkId":5130091959790075998},{"linkId":7}]}')
    def handler(request):
        return httpx.Response(200, content=gz, headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        items = list(c.stream_items("http://h/areaLinkPub", "linkCoordList.item"))
    assert len(items) == 2
    assert items[0]["linkId"] == 5130091959790075998
