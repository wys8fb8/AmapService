from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import upsert_road_links
from amap_service.sdk import LinkInfo, TrackConverter


def _seed(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0)]},
    ])
    return e


def test_forward_track_not_reversed(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e, tolerance_m=30.0, reverse_angle_deg=90.0)
    infos = conv.linetrack_to_linkinfos("120.0,31.0;120.001,31.0;120.002,31.0")
    assert infos == [LinkInfo(link_id=1, reverse_coords=False)]


def test_reverse_track_flagged(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e, tolerance_m=30.0, reverse_angle_deg=90.0)
    infos = conv.linetrack_to_linkinfos("120.002,31.0;120.001,31.0;120.0,31.0")
    assert infos == [LinkInfo(link_id=1, reverse_coords=True)]


def test_no_match_returns_empty(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e, tolerance_m=30.0)
    assert conv.linetrack_to_linkinfos("120.0,31.5") == []
    assert conv.linetrack_to_linkinfos("") == []


def test_linkinfos_to_tracks_forward(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e)
    s = conv.linkinfos_to_tracks([LinkInfo(link_id=1, reverse_coords=False)])
    assert s == "120.0,31.0;120.001,31.0;120.002,31.0"


def test_linkinfos_to_tracks_reverse(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e)
    s = conv.linkinfos_to_tracks([LinkInfo(link_id=1, reverse_coords=True)])
    assert s == "120.002,31.0;120.001,31.0;120.0,31.0"


def test_linkinfos_to_tracks_empty(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e)
    assert conv.linkinfos_to_tracks([]) == ""


def test_link_tracks_per_segment_reversed_when_flagged(tmp_path):
    e = _seed(tmp_path)
    conv = TrackConverter(e)
    tracks = conv.link_tracks([
        LinkInfo(link_id=1, reverse_coords=False),
        LinkInfo(link_id=1, reverse_coords=True),
    ])
    assert tracks == [
        "120.0,31.0;120.001,31.0;120.002,31.0",     # forward
        "120.002,31.0;120.001,31.0;120.0,31.0",     # same link, reversed for reverse_coords
    ]
    assert conv.link_tracks([]) == []


def test_linetrack_to_segments_emits_full_link_geometry(tmp_path):
    # link spans 120.0..120.003; even if the bus track only covers the middle, we emit the
    # FULL link geometry (real networks are fine-grained — the link IS the segment, no clipping)
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [{
        "link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
        "line_track": "", "coords": [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0), (120.003, 31.0)],
    }])
    conv = TrackConverter(e, tolerance_m=30.0)
    segs = conv.linetrack_to_segments("120.001,31.0;120.002,31.0")
    assert len(segs) == 1 and segs[0]["link_id"] == 1 and segs[0]["reverse_coords"] is False
    from amap_service.sdk import geometry as g
    pts = g.parse_track(segs[0]["line_track"])
    assert pts == [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0), (120.003, 31.0)]  # full link


def test_linetrack_to_segments_reverse_orientation(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [{
        "link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
        "line_track": "", "coords": [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0)],
    }])
    conv = TrackConverter(e, tolerance_m=30.0)
    segs = conv.linetrack_to_segments("120.002,31.0;120.001,31.0;120.0,31.0")
    assert segs[0]["reverse_coords"] is True
    from amap_service.sdk import geometry as g
    pts = g.parse_track(segs[0]["line_track"])
    assert pts[0][0] > pts[-1][0]   # emitted in travel (descending) order


def test_segments_have_two_points_even_with_one_gps_point_per_link(tmp_path):
    # two connected north-bound links; a sparse 2-point track lands 1 point on each link.
    # full-link emit yields each link's complete geometry (>=2 pts) — no single-point segments.
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0), (0.0, 0.002)]},
        {"link_id": 2, "road_name": "B", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.002), (0.0, 0.004)]},
    ])
    conv = TrackConverter(e, tolerance_m=50.0)
    segs = conv.linetrack_to_segments("0.0,0.0005;0.0,0.0035")
    from amap_service.sdk import geometry as g
    assert [s["link_id"] for s in segs] == [1, 2]
    for s in segs:
        assert len(g.parse_track(s["line_track"])) >= 2   # no single-point segments


def test_assemble_track_joins_segments_dedup_seam(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    conv = TrackConverter(e)
    segs = [
        {"link_id": 1, "reverse_coords": False, "line_track": "120.0,31.0;120.001,31.0"},
        {"link_id": 2, "reverse_coords": False, "line_track": "120.001,31.0;120.002,31.0"},  # shared seam
    ]
    assert conv.assemble_track(segs) == "120.0,31.0;120.001,31.0;120.002,31.0"


def test_second_pass_runs_and_is_continuous(tmp_path):
    # two connected links; passes=2 must still produce the connected chain (no crash, continuous)
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.0, 31.0), (120.002, 31.0)]},
        {"link_id": 2, "road_name": "B", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(120.002, 31.0), (120.004, 31.0)]},
    ])
    conv = TrackConverter(e, tolerance_m=50.0)
    segs = conv.linetrack_to_segments("120.0005,31.0;120.0015,31.0;120.0025,31.0;120.0035,31.0",
                                      passes=2, densify_step_m=20.0)
    assert [s["link_id"] for s in segs] == [1, 2]


def test_drop_against_track_removes_opposite_segment(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    conv = TrackConverter(e)
    coords = [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0), (120.003, 31.0)]   # eastbound track
    segs = [
        {"link_id": 1, "reverse_coords": False, "line_track": "120.0,31.0;120.001,31.0"},      # with route
        {"link_id": 2, "reverse_coords": False, "line_track": "120.0015,31.0;120.0012,31.0"},   # against route
        {"link_id": 3, "reverse_coords": False, "line_track": "120.002,31.0;120.003,31.0"},
    ]
    out = conv._drop_against_track(segs, coords)
    assert [s["link_id"] for s in out] == [1, 3]


def test_drop_loops_removes_return_to_start_excursion(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    conv = TrackConverter(e)
    segs = [
        {"link_id": 1, "reverse_coords": False, "line_track": "120.0,31.0;120.002,31.0"},
        {"link_id": 2, "reverse_coords": False, "line_track": "120.002,31.0;120.002,31.0005"},   # out
        {"link_id": 3, "reverse_coords": False, "line_track": "120.002,31.0005;120.002,31.0"},    # back
        {"link_id": 4, "reverse_coords": False, "line_track": "120.002,31.0;120.004,31.0"},        # resume
    ]
    out = conv._drop_loops(segs)
    assert [s["link_id"] for s in out] == [1, 4]


def test_drop_juts_removes_perpendicular_but_keeps_real_turn(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    conv = TrackConverter(e)
    east_track = [(120.0, 31.0), (120.001, 31.0), (120.002, 31.0)]
    # JUT: neighbors head east, middle darts ~44m north OFF the track -> remove
    jut = [
        {"link_id": 1, "reverse_coords": False, "line_track": "120.0,31.0;120.001,31.0"},      # east, on track
        {"link_id": 2, "reverse_coords": False, "line_track": "120.001,31.0;120.001,31.0004"},  # north, off track
        {"link_id": 3, "reverse_coords": False, "line_track": "120.001,31.0;120.002,31.0"},      # east, on track
    ]
    assert [s["link_id"] for s in conv._drop_juts([dict(s) for s in jut], east_track)] == [1, 3]
    # ON-TRACK detour: same jut angles, but the middle segment lies ON the track -> kept
    on_track = [(120.0, 31.0), (120.001, 31.0), (120.001, 31.0004), (120.002, 31.0)]
    keep = [dict(s) for s in jut]
    assert [s["link_id"] for s in conv._drop_juts(keep, on_track)] == [1, 2, 3]
    # REAL TURN: neighbors head different ways (east then north) -> the turn segment is kept
    turn = [
        {"link_id": 1, "reverse_coords": False, "line_track": "120.0,31.0;120.001,31.0"},        # east
        {"link_id": 2, "reverse_coords": False, "line_track": "120.001,31.0;120.001,31.0004"},    # north (the turn)
        {"link_id": 3, "reverse_coords": False, "line_track": "120.001,31.0004;120.001,31.001"},  # north (continues)
    ]
    assert [s["link_id"] for s in conv._drop_juts([dict(s) for s in turn], east_track)] == [1, 2, 3]


def test_undivided_out_and_back_yields_link_twice(tmp_path):
    # one undivided link; bus drives up it then back down -> same link appears twice
    # (reverse_coords False outbound, True on the return), and the legit return is NOT pruned.
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [{
        "link_id": 1, "road_name": "单车道路", "length": 1, "formway": 15, "roadclass": 9,
        "line_track": "", "coords": [(0.0, 31.0 + 0.0002 * k) for k in range(16)],
    }])
    conv = TrackConverter(e, tolerance_m=40.0)
    from amap_service.sdk import geometry as g
    out = [(0.0, 31.0002 + 0.00013 * k) for k in range(20)]
    back = [(0.0, out[-1][1] - 0.00013 * k) for k in range(20)]
    segs = conv.linetrack_to_segments(g.format_track(out + back), passes=1)
    assert [(s["link_id"], s["reverse_coords"]) for s in segs] == [(1, False), (1, True)]


def test_doubled_track_mask_flags_turnaround():
    # north then south over the same line -> the overlapping vertices are flagged doubled
    from amap_service.sdk import geometry as g
    out = [(0.0, 31.0 + 0.0002 * k) for k in range(12)]
    back = [(0.0, out[-1][1] - 0.0002 * k) for k in range(12)]
    coords = out + back
    mask = TrackConverter._doubled_track_mask(coords)
    assert any(mask)                  # doubling detected
    assert sum(mask) >= 6             # a good chunk of the overlap is flagged
