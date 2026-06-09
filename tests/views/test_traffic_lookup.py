from amap_service.views.traffic_lookup import DictTrafficLookup


def test_lookup_returns_only_known_ids():
    rows = [{"link_id": 1, "state": 2, "speed": 18, "travel_time": 35,
             "traffic_time": "2026-06-09 13:02:00"}]
    lk = DictTrafficLookup(rows)
    out = lk.get_latest_traffic([1, 999])
    assert out[1]["state"] == 2
    assert out[1]["speed"] == 18
    assert out[1]["traffic_time"] == "2026-06-09 13:02:00"
    assert 999 not in out


def test_lookup_empty():
    assert DictTrafficLookup([]).get_latest_traffic([1]) == {}
