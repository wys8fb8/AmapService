from amap_service.sdk.station_traffic import build_chain, section_links


def _seg(link_id, track):
    return {"link_id": link_id, "line_track": track}


def test_build_chain_arc_intervals_contiguous():
    # two links end-to-end along the equator; ~111.32 m per 0.001° lng
    chain = build_chain([
        _seg(1, "0.0,0.0;0.001,0.0"),
        _seg(2, "0.001,0.0;0.002,0.0"),
    ])
    assert [c.link_id for c in chain] == [1, 2]
    assert chain[0].arc_start == 0.0
    # link2 starts exactly where link1 ends (shared arc scale)
    assert abs(chain[0].arc_end - chain[1].arc_start) < 1e-9
    assert chain[1].arc_end > chain[1].arc_start > 0.0


def test_build_chain_skips_degenerate_segments():
    chain = build_chain([_seg(1, ""), _seg(2, "5,5"), _seg(3, "0.0,0.0;0.001,0.0")])
    assert [c.link_id for c in chain] == [3]  # empty + single-point dropped


def test_section_links_overlap_lengths():
    chain = build_chain([
        _seg(1, "0.0,0.0;0.001,0.0"),   # arc ~ [0, L]
        _seg(2, "0.001,0.0;0.002,0.0"),  # arc ~ [L, 2L]
    ])
    L = chain[0].arc_end
    # span from mid-link1 to mid-link2 -> half of link1 + half of link2
    pairs = section_links(chain, L * 0.5, L * 1.5)
    assert [lid for lid, _ in pairs] == [1, 2]
    assert abs(pairs[0][1] - L * 0.5) < 1e-6
    assert abs(pairs[1][1] - L * 0.5) < 1e-6


def test_section_links_zero_length_span_returns_covering_link_full():
    chain = build_chain([_seg(7, "0.0,0.0;0.001,0.0")])
    mid = chain[0].arc_end * 0.5
    pairs = section_links(chain, mid, mid)  # zero-length
    assert len(pairs) == 1 and pairs[0][0] == 7 and pairs[0][1] == 1.0


from amap_service.sdk.station_traffic import _link_at


def test_link_at_clamps_out_of_range():
    chain = build_chain([
        _seg(1, "0.0,0.0;0.001,0.0"),
        _seg(2, "0.001,0.0;0.002,0.0"),
    ])
    assert _link_at(chain, -50.0) == 1          # before chain start -> first link
    assert _link_at(chain, chain[-1].arc_end + 50.0) == 2  # past chain end -> last link
    assert _link_at([], 0.0) is None            # empty chain


from amap_service.sdk.station_traffic import largest_remainder


def test_largest_remainder_sums_to_100():
    assert sum(largest_remainder([1.0, 1.0, 1.0])) == 100
    assert largest_remainder([1.0, 1.0, 1.0]) == [33, 33, 34]  # 余 1 给最大余数


def test_largest_remainder_proportional():
    out = largest_remainder([50.0, 30.0, 20.0])
    assert out == [50, 30, 20] and sum(out) == 100


def test_largest_remainder_edge_cases():
    assert largest_remainder([]) == []
    assert largest_remainder([5.0]) == [100]
    assert largest_remainder([0.0, 0.0]) == [0, 0]  # 总长 0 不分配


from amap_service.sdk.station_traffic import sample_chain, align_stations


def test_sample_chain_monotonic_arcs_cover_endpoints():
    chain = build_chain([_seg(1, "0.0,0.0;0.001,0.0;0.002,0.0")])
    samples = sample_chain(chain, step_m=20.0)
    arcs = [a for a, _ in samples]
    assert arcs == sorted(arcs)               # 非递减
    assert abs(arcs[0]) < 1e-6                 # 含起点
    assert abs(arcs[-1] - chain[0].arc_end) < 1.0  # 含终点


def test_align_stations_out_and_back_disambiguates():
    # 单车道回头：去程 0->0.01°，回程 0.01°->0（同一几何反向），同一 link 两遍
    out = "0.0,0.0;0.0025,0.0;0.005,0.0;0.0075,0.0;0.01,0.0"
    back = "0.01,0.0;0.0075,0.0;0.005,0.0;0.0025,0.0;0.0,0.0"
    chain = build_chain([_seg(1, out), _seg(1, back)])
    total = chain[-1].arc_end
    samples = sample_chain(chain, step_m=20.0)
    # 4 个站：去程近(0.002), 远端(0.0095), 回程(0.005), 回程近起点(0.001)
    stations = [(0.002, 0.0), (0.0095, 0.0), (0.005, 0.0), (0.001, 0.0)]
    arcs = align_stations(samples, stations)
    assert arcs == sorted(arcs)        # 单调
    assert arcs[0] < total / 2         # 首站在去程腿
    assert arcs[3] > total / 2         # 末站(空间上贴起点)被正确推到回程腿
    assert arcs[2] > total / 2         # 第3站也在回程腿


def test_align_stations_empty_inputs():
    assert align_stations([], [(0.0, 0.0)]) == [0.0]
    assert align_stations([(0.0, (0.0, 0.0))], []) == []


from amap_service.sdk.station_traffic import _stations_for, _line_name_of


def _line_obj():
    return {"Data": {
        "LineName": "192",
        "UpObject": {"UpDown": 0, "Stations": [
            {"LevelId": 2, "Lon02": 121.42, "Lat02": 31.13},
            {"LevelId": 1, "Lon02": 121.40, "Lat02": 31.10},  # 乱序，应按 LevelId 排
            {"LevelId": 3, "Lon02": 121.44, "Lat02": 31.16},
        ]},
        "DownObject": None,
    }}


def test_stations_for_sorted_by_levelid():
    st = _stations_for(_line_obj(), 0)
    assert [lvl for lvl, _, _ in st] == [1, 2, 3]
    assert st[0] == (1, 121.40, 31.10)


def test_stations_for_missing_direction_empty():
    assert _stations_for(_line_obj(), 1) == []          # DownObject 为 None
    assert _stations_for({"Data": {}}, 0) == []


def test_stations_for_drops_incomplete_station():
    obj = {"Data": {"UpObject": {"Stations": [
        {"LevelId": 1, "Lon02": None, "Lat02": 31.1},   # 缺坐标 -> 丢
        {"LevelId": 2, "Lon02": 121.4, "Lat02": 31.1},
    ]}}}
    assert [lvl for lvl, _, _ in _stations_for(obj, 0)] == [2]


def test_line_name_of():
    assert _line_name_of(_line_obj()) == "192"
    assert _line_name_of({"Data": {}}) == ""


from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import replace_transit_segments, upsert_traffic_status
from amap_service.sdk.station_traffic import StationTrafficResolver


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _line_two_links():
    # 直线 3 个站；链=link1[0,0.001]+link2[0.001,0.002]
    return {"Data": {"LineName": "T1", "UpObject": {"Stations": [
        {"LevelId": 1, "Lon02": 0.0, "Lat02": 0.0},
        {"LevelId": 2, "Lon02": 0.001, "Lat02": 0.0},   # 落在两 link 接缝
        {"LevelId": 3, "Lon02": 0.002, "Lat02": 0.0},
    ]}}}


def test_station_section_basic_pct_and_state(tmp_path):
    e = _engine(tmp_path)
    replace_transit_segments(e, "T1", 0, None, [
        {"link_id": 1, "reverse_coords": 0, "line_track": "0.0,0.0;0.001,0.0"},
        {"link_id": 2, "reverse_coords": 0, "line_track": "0.001,0.0;0.002,0.0"},
    ])
    upsert_traffic_status(e, [{"link_id": 1, "state": 3}])  # link2 无记录 -> 默认 1
    r = StationTrafficResolver(e)
    # 站2 区间 = 站1->站2 = 整条 link1
    sec = r.station_section(_line_two_links(), 0, 2)
    assert sec == [{"link_id": 1, "state": 3, "pct": 100}]
    # 站3 区间 = 站2->站3 = 整条 link2，路况缺失默认 1
    sec3 = r.station_section(_line_two_links(), 0, 3)
    assert sec3 == [{"link_id": 2, "state": 1, "pct": 100}]


def test_station_section_invalid_inputs(tmp_path):
    e = _engine(tmp_path)
    r = StationTrafficResolver(e)
    # 无 transit_segment
    assert r.station_section(_line_two_links(), 0, 2) == []
    replace_transit_segments(e, "T1", 0, None, [
        {"link_id": 1, "reverse_coords": 0, "line_track": "0.0,0.0;0.001,0.0"}])
    assert r.station_section(_line_two_links(), 0, 1) == []   # level_id<2
    assert r.station_section(_line_two_links(), 0, 99) == []  # 越界


from amap_service.sdk import StationTrafficResolver as ExportedResolver


def test_line_sections_shape_and_pct_sum(tmp_path):
    e = _engine(tmp_path)
    replace_transit_segments(e, "T1", 0, None, [
        {"link_id": 1, "reverse_coords": 0, "line_track": "0.0,0.0;0.001,0.0"},
        {"link_id": 2, "reverse_coords": 0, "line_track": "0.001,0.0;0.002,0.0"},
    ])
    upsert_traffic_status(e, [{"link_id": 1, "state": 2}])
    out = StationTrafficResolver(e).line_sections(_line_two_links())
    assert set(out.keys()) == {0}                         # 只有上行
    dir0 = out[0]
    assert [list(d.keys())[0] for d in dir0] == [2, 3]    # 从第2站起，每站一条
    for d in dir0:
        entries = list(d.values())[0]
        assert sum(x["pct"] for x in entries) == 100      # 每区间和=100
    assert dir0[0][2] == [{"link_id": 1, "state": 2, "pct": 100}]


def test_line_sections_skips_direction_without_segments(tmp_path):
    e = _engine(tmp_path)  # 没有任何 transit_segment
    assert StationTrafficResolver(e).line_sections(_line_two_links()) == {}


def test_resolver_is_exported():
    assert ExportedResolver is StationTrafficResolver
