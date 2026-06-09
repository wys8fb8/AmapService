import json
import fakeredis
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.cache.client import RedisCache, NoOpCache
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import upsert_traffic_status
from amap_service.sdk import TrafficReader


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_get_latest_traffic_redis_hit_and_db_fallback(tmp_path):
    e = _engine(tmp_path)
    upsert_traffic_status(e, [
        {"link_id": 1, "speed": 10, "state": 1, "travel_time": 5, "traffic_time": "t1"},
        {"link_id": 2, "speed": 20, "state": 2, "travel_time": 6, "traffic_time": "t2"},
    ])
    cache = RedisCache(fakeredis.FakeRedis())
    cache.set("traffic:latest:1", json.dumps(
        {"link_id": 1, "speed": 99, "state": 3, "travel_time": 7, "traffic_time": "t9"}))
    out = TrafficReader(e, cache).get_latest_traffic([1, 2, 3])
    assert out[1] == {"state": 3, "speed": 99, "travel_time": 7, "traffic_time": "t9"}  # Redis 优先
    assert out[2] == {"state": 2, "speed": 20, "travel_time": 6, "traffic_time": "t2"}  # DB 回落
    assert 3 not in out                                                                  # 两处都无


def test_get_latest_traffic_no_cache_pure_db(tmp_path):
    e = _engine(tmp_path)
    upsert_traffic_status(e, [{"link_id": 1, "speed": 10, "state": 1,
                               "travel_time": 5, "traffic_time": "t1"}])
    r = TrafficReader(e)                       # cache=None
    assert r.get_latest_traffic([1])[1]["state"] == 1
    r2 = TrafficReader(e, NoOpCache())         # 禁用缓存
    assert r2.get_latest_traffic([1])[1]["state"] == 1
    assert r.get_latest_traffic([]) == {}


def test_get_latest_traffic_dedups_ids(tmp_path):
    e = _engine(tmp_path)
    upsert_traffic_status(e, [{"link_id": 1, "speed": 10, "state": 1,
                               "travel_time": 5, "traffic_time": "t1"}])
    assert list(TrafficReader(e).get_latest_traffic([1, 1, 1]).keys()) == [1]
