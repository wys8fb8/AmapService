from sqlalchemy import select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_section_link
from amap_service.db.repositories import replace_transit_segments, replace_transit_stations
from amap_service.pipelines.section_build import run_section_build


class _Sdk:
    section_sample_step_m = 4.0


class _Cfg:
    sdk = _Sdk()


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_section_build_end_to_end(tmp_path):
    e = _engine(tmp_path)
    replace_transit_segments(e, "T1", 0, "00T1", [
        {"link_id": 1, "reverse_coords": 0, "line_track": "0.0,0.0;0.001,0.0"},
        {"link_id": 2, "reverse_coords": 0, "line_track": "0.001,0.0;0.002,0.0"},
    ])
    replace_transit_stations(e, "T1", 0, "00T1", [
        {"level_id": 1, "level_name": "A", "longitude": 0.0, "latitude": 0.0},
        {"level_id": 2, "level_name": "B", "longitude": 0.001, "latitude": 0.0},
        {"level_id": 3, "level_name": "C", "longitude": 0.002, "latitude": 0.0},
    ])
    stats = run_section_build(e, _Cfg())
    assert stats["directions"] == 1 and stats["sections"] >= 1
    assert stats["lines"] == 1
    with e.connect() as c:
        rows = c.execute(
            select(transit_section_link.c.to_level_id, transit_section_link.c.link_id,
                   transit_section_link.c.pct)
            .where(transit_section_link.c.line_name == "T1")
            .order_by(transit_section_link.c.to_level_id, transit_section_link.c.seq)
        ).all()
    assert (2, 1, 100) in [(r.to_level_id, r.link_id, r.pct) for r in rows]
    assert (3, 2, 100) in [(r.to_level_id, r.link_id, r.pct) for r in rows]


def test_section_build_skips_direction_without_stations(tmp_path):
    e = _engine(tmp_path)
    replace_transit_segments(e, "T1", 0, None, [
        {"link_id": 1, "reverse_coords": 0, "line_track": "0.0,0.0;0.001,0.0"}])
    stats = run_section_build(e, _Cfg())
    with e.connect() as c:
        cnt = c.execute(select(transit_section_link.c.id)).all()
    assert cnt == []
    assert stats["sections"] == 0
    assert stats["directions"] == 0 and stats["lines"] == 0
