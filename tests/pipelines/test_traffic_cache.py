import json
import httpx
import fakeredis
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.cache.client import RedisCache
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import traffic_status
from amap_service.pipelines.traffic import run_traffic

PAYLOAD = {"linkStates": [
    {"linkId": 1, "speed": 80, "state": 1, "travelTime": 10},
    {"linkId": 2, "speed": 50, "state": 2, "travelTime": 20},
]}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client():
    return HttpClient(backoff_seconds=0,
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD)))


def test_incremental_skips_unchanged_on_second_run(tmp_path):
    e = _engine(tmp_path)
    cache = RedisCache(fakeredis.FakeRedis())
    s1 = run_traffic(e, _client(), "http://h", "/p", cache=cache, incremental=True)
    assert s1["written"] == 2
    s2 = run_traffic(e, _client(), "http://h", "/p", cache=cache, incremental=True)
    assert s2["written"] == 0 and s2["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 2


def test_snapshot_written_to_cache(tmp_path):
    e = _engine(tmp_path)
    r = fakeredis.FakeRedis()
    cache = RedisCache(r)
    run_traffic(e, _client(), "http://h", "/p", cache=cache, snapshot=True)
    snap = json.loads(r.get("traffic:latest:1").decode())
    assert snap["speed"] == 80 and snap["state"] == 1 and snap["travel_time"] == 10


def test_no_cache_path_unchanged(tmp_path):
    e = _engine(tmp_path)
    s = run_traffic(e, _client(), "http://h", "/p")
    assert s["written"] == 2
