import httpx
import pytest
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import road_link, road_link_coord
from amap_service.pipelines.road_network import run_road_network

PAYLOAD = {
    "linkCoordList": [
        {"linkId": 5130091959790075998,
         "coordList": [120.93746244907379, 31.06035053730011, 120.9343296289444, 31.05913281440735],
         "roadName": "G50沪渝高速", "length": 328, "formway": 1, "roadclass": 0},
        {"linkId": 5130091959790075999,
         "coordList": [120.9343296289444, 31.05913281440735, 120.9331226348877, 31.058687567710876],
         "roadName": "G50沪渝高速", "length": 125, "formway": 1, "roadclass": 0},
    ]
}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client():
    def handler(request):
        assert request.url.path == "/g5_server/map/api/areaLinkPub"
        return httpx.Response(200, json=PAYLOAD)
    return HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("mode", ["memory", "stream"])
def test_run_road_network_both_modes(tmp_path, mode):
    e = _engine(tmp_path)
    client = _client()
    stats = run_road_network(e, client, "http://192.168.102.102:8080",
                             "/g5_server/map/api/areaLinkPub", parse_mode=mode)
    assert stats["inserted"] == 2 and stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 2
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 4
        assert c.execute(select(road_link.c.link_id).order_by(road_link.c.link_id)
                         ).scalars().first() == 5130091959790075998
        lng = c.execute(select(road_link_coord.c.longitude).order_by(road_link_coord.c.id)).scalars().first()
        assert isinstance(lng, float)
    client.close()


def test_invalid_mode_raises(tmp_path):
    e = _engine(tmp_path)
    client = _client()
    with pytest.raises(ValueError):
        run_road_network(e, client, "http://h", "/g5_server/map/api/areaLinkPub", parse_mode="bogus")
    client.close()
