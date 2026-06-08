import httpx
import pytest
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import traffic_status
from amap_service.pipelines.traffic import run_traffic

PAYLOAD = {
    "autolrDataVersion": "3.26.05.17",
    "linkStates": [
        {"linkId": 5130516143645130888, "speed": 89, "state": 1, "travelTime": 59},
        {"linkId": 5130516143645131894, "listSectionStatus": [
            {"offset": 3765, "reliability": 89, "speed": 88, "state": 1, "travelTime": 688},
            {"offset": 2165, "reliability": 89, "speed": 92, "state": 1, "travelTime": 307},
        ]},
    ],
}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


@pytest.mark.parametrize("mode", ["memory", "stream"])
def test_run_traffic_both_modes(tmp_path, mode):
    e = _engine(tmp_path)
    def handler(request):
        assert request.url.path == "/g5_server/map/api/traffic/status"
        return httpx.Response(200, json=PAYLOAD)
    client = HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))
    stats = run_traffic(e, client, "http://192.168.102.102:8080/",
                        "/g5_server/map/api/traffic/status", parse_mode=mode)
    assert stats["inserted"] == 2 and stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 2
        agg = c.execute(
            select(traffic_status.c.speed, traffic_status.c.state, traffic_status.c.travel_time)
            .where(traffic_status.c.link_id == 5130516143645131894)
        ).one()
        assert tuple(agg) == (89, 1, 995)
    client.close()


def test_run_traffic_upsert_refresh(tmp_path):
    e = _engine(tmp_path)
    payloads = [
        {"linkStates": [{"linkId": 1, "speed": 80, "state": 1, "travelTime": 10}]},
        {"linkStates": [{"linkId": 1, "speed": 20, "state": 3, "travelTime": 40}]},
    ]
    def handler(request):
        return httpx.Response(200, json=payloads.pop(0))
    client = HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))
    run_traffic(e, client, "http://h", "/g5_server/map/api/traffic/status")
    run_traffic(e, client, "http://h", "/g5_server/map/api/traffic/status")
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 1
        assert tuple(c.execute(select(traffic_status.c.speed, traffic_status.c.state)).one()) == (20, 3)
    client.close()
