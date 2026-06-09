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


def test_run_traffic_incremental_only_writes_changed(tmp_path):
    e = _engine(tmp_path)
    import fakeredis, json as _json
    from amap_service.cache.client import RedisCache
    import httpx
    from amap_service.clients.base import HttpClient
    cache = RedisCache(fakeredis.FakeRedis())
    payload = {"linkStates": [
        {"linkId": 1, "speed": 80, "state": 1, "travelTime": 10},
        {"linkId": 2, "speed": 50, "state": 2, "travelTime": 20},
    ]}
    def _client_for(p):
        return HttpClient(backoff_seconds=0,
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=p)))
    c1 = _client_for(payload)
    s1 = run_traffic(e, c1, "http://h", "/g5_server/map/api/traffic/status",
                     cache=cache, incremental=True, snapshot=True); c1.close()
    assert s1["written"] == 2
    c2 = _client_for(payload)
    s2 = run_traffic(e, c2, "http://h", "/g5_server/map/api/traffic/status",
                     cache=cache, incremental=True, snapshot=True); c2.close()
    assert s2["written"] == 0                       # 签名未变 -> 不写
    assert _json.loads(cache.get("traffic:latest:1"))["speed"] == 80   # snapshot 可读


def test_run_traffic_incremental_detects_change(tmp_path):
    e = _engine(tmp_path)
    import fakeredis, httpx
    from amap_service.cache.client import RedisCache
    from amap_service.clients.base import HttpClient
    cache = RedisCache(fakeredis.FakeRedis())
    def _client_for(p):
        return HttpClient(backoff_seconds=0,
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=p)))
    c1 = _client_for({"linkStates": [{"linkId": 1, "speed": 80, "state": 1, "travelTime": 10}]})
    run_traffic(e, c1, "http://h", "/g5_server/map/api/traffic/status",
                cache=cache, incremental=True); c1.close()
    c2 = _client_for({"linkStates": [{"linkId": 1, "speed": 20, "state": 3, "travelTime": 40}]})
    s = run_traffic(e, c2, "http://h", "/g5_server/map/api/traffic/status",
                    cache=cache, incremental=True); c2.close()
    assert s["written"] == 1
    with e.connect() as c:
        assert tuple(c.execute(select(traffic_status.c.speed, traffic_status.c.state)
                               .where(traffic_status.c.link_id == 1)).one()) == (20, 3)


def test_run_traffic_skips_cache_writes_when_db_failed(tmp_path, monkeypatch):
    import fakeredis, httpx
    from amap_service.cache.client import RedisCache
    from amap_service.clients.base import HttpClient
    import amap_service.pipelines.traffic as traffic_mod
    e = _engine(tmp_path)
    cache = RedisCache(fakeredis.FakeRedis())
    # 强制 DB 写入"失败"（全部计为 failed，不抛）
    monkeypatch.setattr(traffic_mod, "upsert_traffic_status",
                        lambda engine, rows: {"written": 0, "failed": len(list(rows))})
    payload = {"linkStates": [{"linkId": 1, "speed": 80, "state": 1, "travelTime": 10}]}
    c = HttpClient(backoff_seconds=0,
                   transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    s = run_traffic(e, c, "http://h", "/g5_server/map/api/traffic/status",
                    cache=cache, incremental=True, snapshot=True)
    c.close()
    assert s["failed"] == 1
    # DB 失败 -> 签名与快照都不应写入（下次可重试）
    assert cache.get("traffic:sig:1") is None
    assert cache.get("traffic:latest:1") is None
