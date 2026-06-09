import fakeredis
from amap_service.config.schema import RedisConfig
from amap_service.cache.client import NoOpCache, RedisCache, make_cache


def test_noop_cache_is_inert():
    c = NoOpCache()
    assert c.enabled is False
    assert c.get("k") is None
    c.set("k", "v")
    assert c.get("k") is None


def test_redis_cache_set_get_roundtrip_decodes_str():
    c = RedisCache(fakeredis.FakeRedis())
    assert c.enabled is True
    assert c.get("missing") is None
    c.set("k", "v")
    assert c.get("k") == "v"


def test_redis_cache_ttl():
    r = fakeredis.FakeRedis()
    c = RedisCache(r)
    c.set("k", "v", ttl=100)
    assert c.get("k") == "v"
    assert r.ttl("k") > 0


def test_make_cache_disabled_returns_noop():
    cache = make_cache(RedisConfig(enabled=False))
    assert isinstance(cache, NoOpCache)


def test_make_cache_enabled_returns_redis(monkeypatch):
    import amap_service.cache.client as mod
    monkeypatch.setattr(mod, "_redis_client_from_config", lambda cfg: fakeredis.FakeRedis())
    cache = make_cache(RedisConfig(enabled=True, host="x", port=1))
    assert isinstance(cache, RedisCache)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_redis_cache_mset_mget_roundtrip():
    c = RedisCache(fakeredis.FakeRedis())
    c.mset({"a": "1", "b": "2"})
    assert c.mget(["a", "b", "missing"]) == ["1", "2", None]
    assert c.mget([]) == []


def test_redis_cache_mset_empty_noop():
    c = RedisCache(fakeredis.FakeRedis())
    c.mset({})            # 不报错
    assert c.mget(["x"]) == [None]


def test_redis_cache_mset_ttl():
    r = fakeredis.FakeRedis()
    c = RedisCache(r)
    c.mset({"k": "v"}, ttl=100)
    assert c.get("k") == "v"
    assert r.ttl("k") > 0


def test_noop_cache_batch_inert():
    c = NoOpCache()
    assert c.mget(["a", "b"]) == [None, None]
    c.mset({"a": "1"})    # 不报错
    assert c.mget(["a"]) == [None]
