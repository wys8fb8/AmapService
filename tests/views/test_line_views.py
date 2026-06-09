from amap_service.views.line_views import (
    build_segment_view, build_traffic_view, build_section_view,
    build_station_section_view,
)
from amap_service.views.traffic_lookup import DictTrafficLookup


class FakeCache:
    """最小静态缓存替身：直接给定 segments/sections/link_tracks。"""
    def __init__(self, segments=None, sections=None, link_tracks=None):
        self._segments = segments or {}
        self._sections = sections or {}
        self._link_tracks = link_tracks or {}

    def segments(self, line):
        return self._segments.get(line, {})

    def sections(self, line):
        return self._sections.get(line, {})

    def link_track(self, link_id):
        return self._link_tracks.get(link_id)


LID = 5130091959790075998

SEG_CACHE = FakeCache(segments={"47": {0: [
    {"seq": 0, "link_id": LID, "reverse": 1, "line_track": "121.1,31.1;121.2,31.2"}]}})

SEC_CACHE = FakeCache(
    sections={"47": {0: [
        {"from_level_id": 1, "to_level_id": 2,
         "links": [{"link_id": LID, "length_m": 100.0, "pct": 100}]}]}},
    link_tracks={LID: "121.1,31.1;121.2,31.2"})


def test_segment_view_stringifies_link_id_and_keeps_geometry():
    view = build_segment_view(SEG_CACHE, "47")
    seg = view["directions"][0]["segments"][0]
    assert seg["link_id"] == "5130091959790075998"  # 字符串,防 JS 损精度
    assert seg["reverse"] == 1
    assert seg["line_track"] == "121.1,31.1;121.2,31.2"


def test_segment_view_unknown_line_none():
    assert build_segment_view(SEG_CACHE, "999") is None


def test_traffic_view_lean_no_geometry():
    lk = DictTrafficLookup([{"link_id": LID, "state": 2, "speed": 18,
                             "travel_time": 35, "traffic_time": "T"}])
    view = build_traffic_view(SEG_CACHE, lk, "47", geometry=False)
    assert view["traffic_time"] == "T"
    seg = view["directions"][0]["segments"][0]
    assert seg["link_id"] == "5130091959790075998"
    assert seg["state"] == 2 and seg["speed"] == 18
    assert "line_track" not in seg


def test_traffic_view_with_geometry():
    lk = DictTrafficLookup([{"link_id": LID, "state": 2}])
    view = build_traffic_view(SEG_CACHE, lk, "47", geometry=True)
    assert view["directions"][0]["segments"][0]["line_track"] == "121.1,31.1;121.2,31.2"


def test_traffic_view_missing_traffic_state_none():
    lk = DictTrafficLookup([])
    view = build_traffic_view(SEG_CACHE, lk, "47")
    assert view["directions"][0]["segments"][0]["state"] is None


def test_section_view_default_state_and_pct():
    lk = DictTrafficLookup([])  # 无路况 → 默认 state=1
    view = build_section_view(SEC_CACHE, lk, "47")
    sec = view["directions"][0]["sections"][0]
    assert sec["from_level_id"] == 1 and sec["to_level_id"] == 2
    link = sec["links"][0]
    assert link["link_id"] == "5130091959790075998"
    assert link["state"] == 1
    assert link["pct"] == 100
    assert "line_track" not in link


def test_section_view_geometry_uses_link_track():
    lk = DictTrafficLookup([{"link_id": LID, "state": 3}])
    view = build_section_view(SEC_CACHE, lk, "47", geometry=True)
    link = view["directions"][0]["sections"][0]["links"][0]
    assert link["state"] == 3
    assert link["line_track"] == "121.1,31.1;121.2,31.2"


def test_station_section_view_single_interval():
    lk = DictTrafficLookup([{"link_id": LID, "state": 4}])
    view = build_station_section_view(SEC_CACHE, lk, "47", direction=0, to_level_id=2)
    assert view["to_level_id"] == 2
    assert view["direction"] == 0
    assert view["links"][0]["state"] == 4
    assert view["links"][0]["pct"] == 100


def test_station_section_view_unknown_returns_none():
    lk = DictTrafficLookup([])
    assert build_station_section_view(SEC_CACHE, lk, "47", direction=0, to_level_id=99) is None


def test_direction_filter():
    cache = FakeCache(segments={"47": {
        0: [{"seq": 0, "link_id": LID, "reverse": 0, "line_track": "a"}],
        1: [{"seq": 0, "link_id": LID, "reverse": 0, "line_track": "b"}]}})
    view = build_segment_view(cache, "47", direction=1)
    assert [d["direction"] for d in view["directions"]] == [1]
