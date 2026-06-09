from amap_service.api.schemas import TrafficView, SectionView, SegmentView, LineSummary


def test_traffic_view_accepts_string_link_id():
    v = TrafficView(line_name="47", traffic_time="T", directions=[
        {"direction": 0, "segments": [
            {"seq": 0, "link_id": "5130091959790075998", "state": 2,
             "speed": 18, "travel_time": 35, "reverse": 0}]}])
    assert v.directions[0].segments[0].link_id == "5130091959790075998"


def test_section_view_links():
    v = SectionView(line_name="47", traffic_time=None, directions=[
        {"direction": 0, "sections": [
            {"from_level_id": 1, "to_level_id": 2, "links": [
                {"link_id": "1", "state": 1, "pct": 100}]}]}])
    assert v.directions[0].sections[0].links[0].pct == 100


def test_line_summary():
    s = LineSummary(line_name="47", directions=[0, 1],
                    has_segments=True, has_sections=True, station_count=20)
    assert s.station_count == 20
