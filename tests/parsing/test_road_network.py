from decimal import Decimal
from amap_service.parsing.road_network import parse_road_link_item, parse_road_network

ITEM = {
    "linkId": 5130091959790075998,
    "coordList": [120.93746244907379, 31.06035053730011, 120.9343296289444, 31.05913281440735],
    "roadName": "G50沪渝高速", "length": 328, "formway": 1, "roadclass": 0,
}


def test_item_pairs_coords_and_serializes():
    out = parse_road_link_item(ITEM)
    assert out["link_id"] == 5130091959790075998
    assert out["road_name"] == "G50沪渝高速"
    assert out["length"] == 328 and out["formway"] == 1 and out["roadclass"] == 0
    assert out["coords"] == [(120.93746244907379, 31.06035053730011),
                             (120.9343296289444, 31.05913281440735)]
    assert out["line_track"] == "120.93746244907379,31.06035053730011;120.9343296289444,31.05913281440735"


def test_item_decimal_coords_normalized_to_float():
    item = {"linkId": 1, "coordList": [Decimal("120.9"), Decimal("31.0")]}
    out = parse_road_link_item(item)
    assert out["coords"] == [(120.9, 31.0)]
    assert all(isinstance(v, float) for pt in out["coords"] for v in pt)
    assert out["line_track"] == "120.9,31.0"


def test_item_odd_coordlist_drops_trailing():
    out = parse_road_link_item({"linkId": 1, "coordList": [1.0, 2.0, 3.0]})
    assert out["coords"] == [(1.0, 2.0)]


def test_item_missing_optionals_default_none():
    out = parse_road_link_item({"linkId": 7, "coordList": []})
    assert out["road_name"] is None and out["length"] is None
    assert out["coords"] == [] and out["line_track"] == ""


def test_parse_payload_maps_items():
    out = list(parse_road_network({"linkCoordList": [ITEM, {"linkId": 2, "coordList": [1.0, 2.0]}]}))
    assert [o["link_id"] for o in out] == [5130091959790075998, 2]


def test_parse_payload_empty():
    assert list(parse_road_network({})) == []
    assert list(parse_road_network({"linkCoordList": []})) == []
