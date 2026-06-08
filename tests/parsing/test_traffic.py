from amap_service.parsing.traffic import parse_traffic_item, parse_traffic


def test_top_level_item():
    out = parse_traffic_item({"linkId": 5130516143645130888, "speed": 89, "state": 1, "travelTime": 59})
    assert out == {"link_id": 5130516143645130888, "speed": 89, "state": 1, "travel_time": 59}


def test_section_weighted_speed_and_sum_tt():
    item = {"linkId": 5130516143645131894, "listSectionStatus": [
        {"offset": 3765, "reliability": 89, "speed": 88, "state": 1, "travelTime": 688},
        {"offset": 2165, "reliability": 89, "speed": 92, "state": 1, "travelTime": 307},
    ]}
    assert parse_traffic_item(item) == {"link_id": 5130516143645131894, "speed": 89, "state": 1, "travel_time": 995}


def test_section_state_most_congested_ignores_unknown():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 30, "state": 1, "travelTime": 10},
        {"speed": 10, "state": 3, "travelTime": 20},
        {"speed": 0,  "state": 5, "travelTime": 5},
    ]}
    out = parse_traffic_item(item)
    assert out["state"] == 3 and out["travel_time"] == 35


def test_section_all_unknown_state_5():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 0, "state": 5, "travelTime": 10}, {"speed": 0, "state": 5, "travelTime": 10},
    ]}
    assert parse_traffic_item(item)["state"] == 5


def test_section_zero_tt_arithmetic_mean():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 40, "state": 1, "travelTime": 0}, {"speed": 60, "state": 1, "travelTime": 0},
    ]}
    out = parse_traffic_item(item)
    assert out["speed"] == 50 and out["travel_time"] is None


def test_parse_payload_and_empty():
    payload = {"linkStates": [
        {"linkId": 1, "speed": 80, "state": 1, "travelTime": 10},
        {"linkId": 2, "listSectionStatus": [{"speed": 50, "state": 2, "travelTime": 20}]},
    ]}
    out = list(parse_traffic(payload))
    assert [o["link_id"] for o in out] == [1, 2]
    assert list(parse_traffic({})) == []


def test_top_level_decimal_fields_coerced_to_int():
    from decimal import Decimal
    out = parse_traffic_item({"linkId": 5130516143645130888,
                              "speed": Decimal("89"), "state": Decimal("1"), "travelTime": Decimal("59")})
    assert out == {"link_id": 5130516143645130888, "speed": 89, "state": 1, "travel_time": 59}
    assert all(isinstance(out[k], int) for k in ("speed", "state", "travel_time"))


def test_none_fields_stay_none():
    out = parse_traffic_item({"linkId": 1})
    assert out == {"link_id": 1, "speed": None, "state": None, "travel_time": None}
