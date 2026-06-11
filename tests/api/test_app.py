from fastapi.testclient import TestClient
from sqlalchemy import insert

from amap_service.api.app import create_app
from amap_service.config.loader import load_config
from amap_service.config.schema import (
    AppConfig, ApiConfig, ApiAuthConfig, DatabaseConfig, SqliteConfig,
    RedisConfig,
)
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import (
    road_link, traffic_status, transit_segment, transit_section_link, transit_station,
)

LID = 5130091959790075998


def _base_config(db_path, auth_enabled=False):
    raw = {
        "amap": {"endpoint": "http://x", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "u", "password": "p", "token_url": "http://a",
                    "line_list_url": "http://b", "line_entity_url": "http://c"},
        "database": {"type": "sqlite", "sqlite": {"path": db_path}},
        "redis": {"enabled": False},
        "api": {"enabled": True, "auth": {"enabled": auth_enabled, "api_key": "secret"}},
    }
    return AppConfig.model_validate(raw)


def _seed(eng):
    with eng.begin() as c:
        c.execute(insert(road_link), [{"link_id": LID, "line_track": "121.1,31.1;121.2,31.2"}])
        c.execute(insert(transit_segment), [
            {"line_name": "47", "direction": 0, "seq": 0, "link_id": LID,
             "reverse_coords": 0, "line_track": "121.1,31.1;121.2,31.2"}])
        c.execute(insert(transit_section_link), [
            {"line_name": "47", "direction": 0, "from_level_id": 1, "to_level_id": 2,
             "seq": 0, "link_id": LID, "length_m": 100.0, "pct": 100}])
        c.execute(insert(transit_station), [
            {"line_name": "47", "direction": 0, "level_id": 1, "longitude": 121.1, "latitude": 31.1},
            {"line_name": "47", "direction": 0, "level_id": 2, "longitude": 121.2, "latitude": 31.2}])
        c.execute(insert(traffic_status), [
            {"link_id": LID, "speed": 18, "state": 2, "travel_time": 35,
             "traffic_time": "2026-06-09 13:02:00"}])


def _client(tmp_path, auth_enabled=False):
    db_path = str(tmp_path / "t.db")
    cfg = _base_config(db_path, auth_enabled)
    eng = make_engine(cfg.database)
    init_db(eng)
    _seed(eng)
    app = create_app(cfg, engine=eng)
    return TestClient(app)


def test_health(tmp_path):
    r = _client(tmp_path).get("/api/v1/health")
    assert r.status_code == 200 and r.json()["data"]["status"] == "ok"


def test_lines(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines")
    assert r.status_code == 200
    assert r.json()["data"][0]["line_name"] == "47"


def test_segments_req3(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["link_id"] == "5130091959790075998"
    assert seg["line_track"] == "121.1,31.1;121.2,31.2"


def test_traffic_req4_lean_default(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/traffic")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["state"] == 2 and seg["speed"] == 18
    assert seg.get("line_track") is None


def test_traffic_req4_geometry_true(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/traffic?geometry=true")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["line_track"] == "121.1,31.1;121.2,31.2"


def test_sections_req5(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/sections")
    sec = r.json()["data"]["directions"][0]["sections"][0]
    assert sec["from_level_id"] == 1 and sec["to_level_id"] == 2
    assert sec["links"][0]["pct"] == 100 and sec["links"][0]["state"] == 2


def test_single_section_req5(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/sections/2?direction=0")
    assert r.json()["data"]["to_level_id"] == 2
    assert r.json()["data"]["links"][0]["state"] == 2


def test_unknown_line_404(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/999/segments")
    assert r.status_code == 404


def test_bad_direction_422(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments?direction=5")
    assert r.status_code == 422


def test_auth_required_when_enabled(tmp_path):
    c = _client(tmp_path, auth_enabled=True)
    assert c.get("/api/v1/lines").status_code == 401
    assert c.get("/api/v1/lines", headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/api/v1/health").status_code == 200
