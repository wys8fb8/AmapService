from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import upsert_road_links
from amap_service.sdk.matcher import LinkMatcher, MatchedLink


def _seed(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.000, 31.0), (120.001, 31.0), (120.002, 31.0)]},
        {"link_id": 2, "road_name": "B", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.000, 32.0), (120.001, 32.0), (120.002, 32.0)]},
    ])
    return e


def test_matches_nearest_link(tmp_path):
    e = _seed(tmp_path)
    m = LinkMatcher(e, tolerance_m=30.0)
    result = m.match_track([(120.0, 31.0), (120.001, 31.0), (120.002, 31.0)])
    assert [r.link_id for r in result] == [1]
    assert len(result[0].gps_coords) == 3


def test_unmatched_points_dropped(tmp_path):
    e = _seed(tmp_path)
    m = LinkMatcher(e, tolerance_m=30.0)
    result = m.match_track([(120.0, 31.01)])
    assert result == []


def test_empty_track(tmp_path):
    e = _seed(tmp_path)
    m = LinkMatcher(e, tolerance_m=30.0)
    assert m.match_track([]) == []


def test_consecutive_same_link_collapsed(tmp_path):
    e = _seed(tmp_path)
    m = LinkMatcher(e, tolerance_m=30.0)
    result = m.match_track([(120.0, 31.0), (120.0005, 31.0), (120.001, 31.0), (120.0015, 31.0)])
    assert [r.link_id for r in result] == [1]
    assert isinstance(result[0], MatchedLink)
    assert len(result[0].gps_coords) == 4


def test_prune_detour_island_link(tmp_path):
    # links 1 and 2 are the on-route path (connected end-to-start); link 3 is a long
    # through-road that one stray point snaps to but that detours far from both neighbours.
    from amap_service.config.schema import DatabaseConfig, SqliteConfig
    from amap_service.db.engine import make_engine
    from amap_service.db.migrate import init_db
    from amap_service.db.repositories import upsert_road_links
    eng = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(eng)
    upsert_road_links(eng, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.0000, 31.0), (120.0010, 31.0)]},
        {"link_id": 2, "road_name": "B", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.0010, 31.0), (120.0020, 31.0)]},
        # link 3: a long through-road passing ~near the midpoint but detouring north far away
        {"link_id": 3, "road_name": "C", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.0005, 31.02), (120.00105, 31.0), (120.0005, 31.04)]},
    ])
    m = LinkMatcher(eng, tolerance_m=30.0)
    # eastbound track; middle point sits ~near link3's midpoint as well as on link1/2
    result = m.match_track([(120.0002, 31.0), (120.00105, 31.0), (120.0018, 31.0)])
    ids = [r.link_id for r in result]
    assert 3 not in ids          # the detour island was pruned
    assert ids == [1, 2]


def test_prefers_surface_road_over_overlapping_highway(tmp_path):
    # a surface ordinary road (roadclass 9) and an overlapping highway (roadclass 0, 高速)
    # run along the same line; the highway is marginally closer at the sample point.
    from amap_service.config.schema import DatabaseConfig, SqliteConfig
    from amap_service.db.engine import make_engine
    from amap_service.db.migrate import init_db
    from amap_service.db.repositories import upsert_road_links
    eng = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(eng)
    upsert_road_links(eng, [
        {"link_id": 1, "road_name": "高架", "length": 1, "formway": 1, "roadclass": 0,   # highway, slightly closer
         "line_track": "", "coords": [(120.0, 31.000000), (120.002, 31.000000)]},
        {"link_id": 2, "road_name": "地面路", "length": 1, "formway": 15, "roadclass": 9,  # surface, ~3m away
         "line_track": "", "coords": [(120.0, 30.99997), (120.002, 30.99997)]},
    ])
    m = LinkMatcher(eng, tolerance_m=30.0)
    result = m.match_track([(120.0005, 31.0000), (120.001, 31.0000), (120.0015, 31.0000)])
    assert [r.link_id for r in result] == [2]   # surface ordinary road wins over the closer highway


def test_prune_offtrack_segment_unit():
    # unit-test the off-track prune directly (no DB): middle link bulges ~89m north of the track
    from amap_service.sdk.matcher import LinkMatcher, MatchedLink
    m = LinkMatcher.__new__(LinkMatcher)
    m.track_fit_m = 25.0
    coords = [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0), (120.003, 31.0)]
    geoms = {
        1: [(120.0, 31.0), (120.001, 31.0)],
        2: [(120.0015, 31.0), (120.0015, 31.0008)],   # ~89m off the east-west track
        3: [(120.002, 31.0), (120.003, 31.0)],
    }
    matched = [MatchedLink(1, [], False), MatchedLink(2, [], False), MatchedLink(3, [], False)]
    out = m._prune_offtrack(matched, geoms, coords)
    assert [x.link_id for x in out] == [1, 3]   # the off-track middle segment is dropped
