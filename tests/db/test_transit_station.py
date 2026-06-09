from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_station, transit_section_link
from amap_service.db.repositories import replace_transit_stations, replace_transit_section_links


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_replace_stations_inserts_and_replaces(tmp_path):
    e = _engine(tmp_path)
    n = replace_transit_stations(e, "47", 0, "004700", [
        {"level_id": 1, "level_name": "A", "longitude": 121.4, "latitude": 31.1},
        {"level_id": 2, "level_name": "B", "longitude": 121.5, "latitude": 31.2},
    ])
    assert n == 2
    replace_transit_stations(e, "47", 0, "004700", [
        {"level_id": 1, "level_name": "A2", "longitude": 121.41, "latitude": 31.11}])  # 替换 dir0
    replace_transit_stations(e, "47", 1, "004700", [
        {"level_id": 1, "level_name": "C", "longitude": 121.6, "latitude": 31.3}])    # 另一方向不动
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_station)
                         .where(transit_station.c.direction == 0)).scalar() == 1
        assert c.execute(select(func.count()).select_from(transit_station)
                         .where(transit_station.c.direction == 1)).scalar() == 1
        row = c.execute(select(transit_station.c.level_name, transit_station.c.longitude)
                        .where((transit_station.c.line_name == "47") & (transit_station.c.direction == 0))).one()
        assert row.level_name == "A2"


def test_replace_section_links_ordered_and_idempotent(tmp_path):
    e = _engine(tmp_path)
    n = replace_transit_section_links(e, "47", 0, "004700", [
        {"from_level_id": 1, "to_level_id": 2, "seq": 0, "link_id": 5130091959790075998,
         "length_m": 50.0, "pct": 60},
        {"from_level_id": 1, "to_level_id": 2, "seq": 1, "link_id": 123, "length_m": 33.3, "pct": 40},
    ])
    assert n == 2
    with e.connect() as c:
        rows = c.execute(
            select(transit_section_link.c.seq, transit_section_link.c.link_id,
                   transit_section_link.c.length_m, transit_section_link.c.pct)
            .where((transit_section_link.c.line_name == "47") & (transit_section_link.c.direction == 0))
            .order_by(transit_section_link.c.seq)
        ).all()
    assert [(r.seq, r.link_id, r.pct) for r in rows] == [(0, 5130091959790075998, 60), (1, 123, 40)]
    replace_transit_section_links(e, "47", 0, "004700", [
        {"from_level_id": 2, "to_level_id": 3, "seq": 0, "link_id": 9, "length_m": 10.0, "pct": 100}])
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_section_link)
                         .where(transit_section_link.c.direction == 0)).scalar() == 1
