import json

import httpx
from sqlalchemy import func, select
from amap_service.config.schema import AppConfig, DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import upsert_road_links
from amap_service.db.schema import transit_segment
from amap_service.clients.transit import TransitClient
from amap_service.pipelines.transit_build import run_transit_build

# A single eastbound road link the bus track will map onto.
ROAD = {"link_id": 5130091959790075998, "road_name": "X", "length": 1, "formway": 15, "roadclass": 9,
        "line_track": "", "coords": [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0)]}

# Normal line: UpObject follows the link forward; DownObject is the reverse.
ENTITY = {"Code": -200, "Data": {
    "LineName": "47", "NorCode": "004700", "LineType": "Normal",
    "UpObject": {"UpDown": 0, "LineLonLat": "120.0,31.0;120.001,31.0;120.002,31.0"},
    "DownObject": {"UpDown": 1, "LineLonLat": "120.002,31.0;120.001,31.0;120.0,31.0"},
}}


def _config(line_limit=0, companys=None, lines=None):
    return AppConfig.model_validate({
        "amap": {"endpoint": "http://h", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "yangs", "password": "pw",
                    "token_url": "http://h/token", "line_list_url": "http://h/list",
                    "line_entity_url": "http://h/entity",
                    "token_path": "data.token", "line_name_path": "data",
                    "line_name_field": "Roadline", "line_limit": line_limit,
                    "companys": companys, "lines": lines},
    })


def _engine(tmp_path, seed_road=True):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    if seed_road:
        upsert_road_links(e, [ROAD])
    return e


def _client():
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"Roadline": "47"}]})
        return httpx.Response(200, text=json.dumps(ENTITY))
    return TransitClient(_config().transit, transport=httpx.MockTransport(handler), now_ms=lambda: 1)


def test_build_converts_both_directions_to_segments(tmp_path):
    e = _engine(tmp_path)
    stats = run_transit_build(e, _client(), _config())
    assert stats["token_ok"] is True
    assert stats["lines"] == 1 and stats["directions"] == 2 and stats["segments"] == 2

    with e.connect() as c:
        up = c.execute(
            select(transit_segment.c.link_id, transit_segment.c.reverse_coords,
                   transit_segment.c.line_track)
            .where((transit_segment.c.line_name == "47") & (transit_segment.c.direction == 0))
        ).all()
        down = c.execute(
            select(transit_segment.c.reverse_coords, transit_segment.c.line_track)
            .where((transit_segment.c.line_name == "47") & (transit_segment.c.direction == 1))
        ).all()
    # forward: not reversed, 64-bit link_id intact, track in link order
    assert up == [(5130091959790075998, 0, "120.0,31.0;120.001,31.0;120.002,31.0")]
    # reverse direction: flagged AND stored track reversed to match bus travel direction
    assert down == [(1, "120.002,31.0;120.001,31.0;120.0,31.0")]


def test_build_company_filter_skips_other_companies(tmp_path):
    fetched = []
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [
                {"Roadline": "47", "Company": "巴士一公司"},
                {"Roadline": "300", "Company": "浦东公司"},
            ]})
        fetched.append(request.url.params.get("lineName"))
        return httpx.Response(200, text=json.dumps(ENTITY))
    e = _engine(tmp_path)
    client = TransitClient(_config().transit, transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    stats = run_transit_build(e, client, _config(companys="巴士一公司"))
    assert fetched == ["47"]          # 300 (浦东公司) not fetched
    assert stats["lines"] == 1


def test_build_with_empty_road_network_yields_no_segments(tmp_path):
    e = _engine(tmp_path, seed_road=False)
    stats = run_transit_build(e, _client(), _config())
    assert stats["lines"] == 1 and stats["segments"] == 0   # nothing to match against, but no crash
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_segment)).scalar() == 0


# ---------------------------------------------------------------------------
# Task 6: transit-build also writes transit_station and triggers section-build
# ---------------------------------------------------------------------------

class _FakeTransitT6:
    def __init__(self, entity):
        self._entity = entity

    def get_token(self):
        return "tok", None

    def get_line_list(self, token):
        return json.dumps([{"Roadline": "T1"}])

    def get_line_entity(self, token, name):
        return json.dumps(self._entity)


class _TransitT6:
    token_path = None
    line_name_path = None
    line_name_field = "Roadline"
    company_field = "Company"
    companys = None
    lines = None
    line_limit = 0

    def companys_set(self):
        return None

    def lines_set(self):
        return None


class _SdkT6:
    match_tolerance_m = 30.0
    reverse_angle_deg = 90.0
    against_track_deg = 120.0
    loop_return_m = 10.0
    jut_deg = 60.0
    jut_neighbor_deg = 45.0
    jut_offtrack_m = 15.0
    against_window_frac = 0.2
    against_window_m = 80.0
    connect_gap_m = 8.0
    max_fill_links = 8
    refine_passes = 1
    densify_step_m = 15.0
    section_sample_step_m = 4.0


class _CfgT6:
    transit = _TransitT6()
    sdk = _SdkT6()


def _engine_t6(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_transit_build_writes_stations_and_sections(tmp_path):
    from amap_service.db.schema import transit_station, transit_section_link
    e = _engine_t6(tmp_path)
    upsert_road_links(e, [{"link_id": 1, "road_name": "R", "length": 1, "formway": 15,
                           "roadclass": 9, "line_track": "",
                           "coords": [(0.0, 0.0), (0.0005, 0.0), (0.001, 0.0)]}])
    entity = {"Data": {"LineName": "T1", "NorCode": "00T1", "UpObject": {
        "UpDown": 0, "LineLonLat": "0.0,0.0;0.0005,0.0;0.001,0.0",
        "Stations": [{"LevelId": 1, "LevelName": "A", "Lon02": 0.0, "Lat02": 0.0},
                     {"LevelId": 2, "LevelName": "B", "Lon02": 0.001, "Lat02": 0.0}]}}}
    run_transit_build(e, _FakeTransitT6(entity), _CfgT6())
    with e.connect() as c:
        st = c.execute(select(func.count()).select_from(transit_station)).scalar()
        sec = c.execute(select(func.count()).select_from(transit_section_link)).scalar()
    assert st == 2
    assert sec >= 1
