import httpx
import fakeredis
from amap_service.config.schema import TransitConfig
from amap_service.cache.client import RedisCache
from amap_service.clients.transit import build_signature, build_token_body, TransitClient


def _cfg(**kw):
    base = dict(username="yangs", password="pw",
                token_url="http://h/token", line_list_url="http://h/list",
                line_entity_url="http://h/entity", token_path="data.token")
    base.update(kw)
    return TransitConfig(**base)


def test_signature_matches_note_md_formula():
    assert build_signature("yangs", "pw", 1700000000000) == "c07e2485baf739f80c6d2c4ce952f383"


def test_token_body_format():
    body = build_token_body("yangs", "pw", 1700000000000)
    assert body == "appkey=yangs&sign=c07e2485baf739f80c6d2c4ce952f383&timestamp=1700000000000"


def test_get_token_posts_signed_body_and_extracts():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1700000000000)
    token, raw = client.get_token()
    assert token == "TOK"
    assert seen["method"] == "POST" and seen["url"] == "http://h/token"
    assert seen["body"] == "appkey=yangs&sign=c07e2485baf739f80c6d2c4ce952f383&timestamp=1700000000000"
    assert "TOK" in raw
    client.close()


def test_get_token_memory_cached_second_call_no_request():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    t1, _ = client.get_token()
    t2, raw2 = client.get_token()
    assert t1 == "TOK" and t2 == "TOK"
    assert calls["n"] == 1 and raw2 is None
    client.close()


def test_get_token_redis_cached():
    cache = RedisCache(fakeredis.FakeRedis())
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    c1 = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1,
                       cache=cache, token_cache_enabled=True)
    c1.get_token()
    c2 = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1,
                       cache=cache, token_cache_enabled=True)
    token, raw = c2.get_token()
    assert token == "TOK" and raw is None and calls["n"] == 1
    c1.close(); c2.close()


def test_get_line_list_and_entity_capture_first_on_error():
    def handler(request):
        if request.url.path == "/list":
            assert request.url.params.get("loginname") == "yangs"
            return httpx.Response(500, text="boom")
        assert request.url.params.get("lineName") == "L1"
        return httpx.Response(200, text='{"entity": 1}')
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    assert client.get_line_list("TOK") == "boom"
    assert client.get_line_entity("TOK", "L1") == '{"entity": 1}'
    client.close()


def test_line_calls_send_bearer_authorization():
    seen = {}
    def handler(request):
        seen.setdefault("auth", []).append(request.headers.get("Authorization"))
        return httpx.Response(200, text="{}")
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    client.get_line_list("TOK")
    client.get_line_entity("TOK", "L1")
    assert seen["auth"] == ["Bearer TOK", "Bearer TOK"]
    client.close()


def test_get_line_list_uses_loginname_when_set():
    seen = {}
    def handler(request):
        seen["loginname"] = request.url.params.get("loginname")
        return httpx.Response(200, text="{}")
    client = TransitClient(_cfg(loginname="driver01"),
                           transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    client.get_line_list("TOK")
    assert seen["loginname"] == "driver01"   # dedicated loginname, not the appkey username
    client.close()


def test_get_line_list_falls_back_to_username_when_loginname_absent():
    seen = {}
    def handler(request):
        seen["loginname"] = request.url.params.get("loginname")
        return httpx.Response(200, text="{}")
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    client.get_line_list("TOK")
    assert seen["loginname"] == "yangs"      # falls back to username when loginname is None
    client.close()
