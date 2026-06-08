from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_segment
from amap_service.db.repositories import replace_transit_segments


def _seg(link_id, reverse, track=None):
    return {"link_id": link_id, "reverse_coords": reverse, "line_track": track}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_replace_inserts_ordered_segments(tmp_path):
    e = _engine(tmp_path)
    n = replace_transit_segments(e, "47", 0, "004700",
                                 [_seg(5130091959790075998, False, "1,2;3,4"), _seg(123, True, "9,8;7,6")])
    assert n == 2
    with e.connect() as c:
        rows = c.execute(
            select(transit_segment.c.seq, transit_segment.c.link_id,
                   transit_segment.c.reverse_coords, transit_segment.c.line_track)
            .where((transit_segment.c.line_name == "47") & (transit_segment.c.direction == 0))
            .order_by(transit_segment.c.seq)
        ).all()
    assert [tuple(r) for r in rows] == [
        (0, 5130091959790075998, 0, "1,2;3,4"),   # 64-bit link_id + track stored
        (1, 123, 1, "9,8;7,6"),                    # reverse flag + its track
    ]


def test_replace_is_idempotent_per_line_direction(tmp_path):
    e = _engine(tmp_path)
    replace_transit_segments(e, "47", 0, "004700", [_seg(1, False), _seg(2, False), _seg(3, False)])
    replace_transit_segments(e, "47", 0, "004700", [_seg(9, False)])          # replaces dir 0
    replace_transit_segments(e, "47", 1, "004700", [_seg(7, False), _seg(8, False)])  # other dir untouched
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_segment)
                         .where(transit_segment.c.direction == 0)).scalar() == 1
        assert c.execute(select(func.count()).select_from(transit_segment)
                         .where(transit_segment.c.direction == 1)).scalar() == 2
