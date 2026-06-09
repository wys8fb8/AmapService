from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import replace_transit_section_links, upsert_traffic_status
from amap_service.sdk import StationTrafficResolver
from amap_service.sdk.station_traffic import StationTrafficResolver as DirectResolver


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _seed(e):
    replace_transit_section_links(e, "T1", 0, "00T1", [
        {"from_level_id": 1, "to_level_id": 2, "seq": 0, "link_id": 1, "length_m": 60.0, "pct": 60},
        {"from_level_id": 1, "to_level_id": 2, "seq": 1, "link_id": 2, "length_m": 40.0, "pct": 40},
        {"from_level_id": 2, "to_level_id": 3, "seq": 0, "link_id": 3, "length_m": 10.0, "pct": 100},
    ])
    upsert_traffic_status(e, [{"link_id": 1, "state": 3}, {"link_id": 2, "state": 2}])


def test_station_section_reads_table_and_overlays_traffic(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    r = StationTrafficResolver(e)
    assert r.station_section("T1", 0, 2) == [
        {"link_id": 1, "state": 3, "pct": 60},
        {"link_id": 2, "state": 2, "pct": 40},
    ]
    assert r.station_section("T1", 0, 3) == [{"link_id": 3, "state": 1, "pct": 100}]  # 缺省 1


def test_station_section_empty_when_absent(tmp_path):
    e = _engine(tmp_path)
    r = StationTrafficResolver(e)
    assert r.station_section("T1", 0, 2) == []
    _seed(e)
    assert r.station_section("T1", 0, 99) == []
    assert r.station_section("NOPE", 0, 2) == []


def test_line_sections_shape_and_traffic(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    out = StationTrafficResolver(e).line_sections("T1")
    assert set(out.keys()) == {0}
    dir0 = out[0]
    assert [list(d.keys())[0] for d in dir0] == [2, 3]
    assert dir0[0][2] == [{"link_id": 1, "state": 3, "pct": 60},
                          {"link_id": 2, "state": 2, "pct": 40}]
    for d in dir0:
        assert sum(x["pct"] for x in list(d.values())[0]) == 100


def test_line_sections_empty_when_absent(tmp_path):
    e = _engine(tmp_path)
    assert StationTrafficResolver(e).line_sections("T1") == {}


def test_resolver_exported():
    assert StationTrafficResolver is DirectResolver
