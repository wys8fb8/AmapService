from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import road_link, road_link_coord, traffic_status
from amap_service.db.repositories import upsert_road_links, upsert_traffic_status


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_road_link_insert_then_update_replaces_coords(tmp_path):
    e = _engine(tmp_path)
    link = {
        "link_id": 5130091959790075998, "road_name": "G50沪渝高速",
        "length": 328, "formway": 1, "roadclass": 0,
        "line_track": "120.9374,31.0603;120.9343,31.0591;120.93,31.05",
        "coords": [(120.9374, 31.0603), (120.9343, 31.0591), (120.93, 31.05)],
    }
    stats = upsert_road_links(e, [link])
    assert stats["inserted"] == 1 and stats["updated"] == 0 and stats["failed"] == 0

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 1
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 3
        assert c.execute(select(road_link.c.link_id)).scalar() == 5130091959790075998

    updated = dict(link, road_name="改名路", coords=[(1.0, 2.0), (3.0, 4.0)])
    stats2 = upsert_road_links(e, [updated])
    assert stats2["inserted"] == 0 and stats2["updated"] == 1

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 1
        assert c.execute(select(road_link.c.road_name)).scalar() == "改名路"
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 2


def test_traffic_upsert_latest_only(tmp_path):
    e = _engine(tmp_path)
    rid = 5130516143645130888
    s1 = upsert_traffic_status(e, [{"link_id": rid, "speed": 89, "state": 1, "travel_time": 59,
                                    "traffic_time": "2026-05-18 14:51:04"}])
    assert s1["written"] == 1 and s1["failed"] == 0
    s2 = upsert_traffic_status(e, [{"link_id": rid, "speed": 40, "state": 3, "travel_time": 120,
                                    "traffic_time": "2026-05-18 14:53:04"}])
    assert s2["written"] == 1 and s2["failed"] == 0

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 1
        row = c.execute(
            select(traffic_status.c.speed, traffic_status.c.state, traffic_status.c.travel_time,
                   traffic_status.c.traffic_time)
        ).one()
        assert tuple(row) == (40, 3, 120, "2026-05-18 14:53:04")   # latest值含路况时间


def test_traffic_upsert_bulk_multibatch(tmp_path):
    e = _engine(tmp_path)
    rows = [{"link_id": 1000 + i, "speed": i % 100, "state": (i % 4) + 1,
             "travel_time": i, "traffic_time": "2026-05-18 14:51:04"} for i in range(12000)]
    s = upsert_traffic_status(e, rows, batch_size=5000)   # 跨 3 批
    assert s["written"] == 12000 and s["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 12000
    rows2 = [{"link_id": 1000 + i, "speed": 7, "state": 2, "travel_time": 0,
              "traffic_time": "2026-05-18 15:00:00"} for i in range(12000)]
    s2 = upsert_traffic_status(e, rows2, batch_size=5000)
    assert s2["written"] == 12000
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 12000
        assert c.execute(select(traffic_status.c.speed).where(traffic_status.c.link_id == 1000)).scalar() == 7


def test_road_link_dup_in_batch_counts_distinct(tmp_path):
    e = _engine(tmp_path)
    link = {"link_id": 100, "road_name": "a", "length": 1, "formway": 1,
            "roadclass": 0, "line_track": "1,2", "coords": [(1.0, 2.0)]}
    stats = upsert_road_links(e, [link, dict(link, road_name="b")])
    assert stats["inserted"] == 1
    assert stats["updated"] == 0
    assert stats["skipped"] == 1
    assert stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 1


def test_traffic_dup_in_batch_counts_distinct(tmp_path):
    e = _engine(tmp_path)
    row = {"link_id": 200, "speed": 10, "state": 1, "travel_time": 5}
    stats = upsert_traffic_status(e, [row, dict(row, speed=20)])
    assert stats["written"] == 2
    assert stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 1
