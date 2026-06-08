from amap_service.parsing.transit import parse_line_tracks

# Real GetRoadLineEntity shape (久事): Data with Up/Down objects carrying LineLonLat.
NORMAL = {"Code": -200, "Msg": "成功", "Data": {
    "LineName": "47", "NorCode": "004700", "LineType": "Normal",
    "UpObject": {"LineName": "47", "UpDown": 0, "LineLonLat": "121.5,31.2;121.6,31.2"},
    "DownObject": {"LineName": "47", "UpDown": 1, "LineLonLat": "121.6,31.2;121.5,31.2"},
}}

SINGLE_LOOP = {"Code": -200, "Data": {
    "LineName": "192", "NorCode": "019200", "LineType": "SingleLoop",
    "UpObject": {"UpDown": 0, "LineLonLat": "121.42,31.13;121.43,31.14"},
    "DownObject": None,
}}


def test_parse_normal_line_two_directions():
    tracks = parse_line_tracks(NORMAL)
    assert len(tracks) == 2
    assert tracks[0] == {"line_name": "47", "nor_code": "004700", "direction": 0,
                         "track": "121.5,31.2;121.6,31.2"}
    assert tracks[1]["direction"] == 1 and tracks[1]["track"] == "121.6,31.2;121.5,31.2"


def test_parse_single_loop_one_direction():
    tracks = parse_line_tracks(SINGLE_LOOP)
    assert len(tracks) == 1
    assert tracks[0]["line_name"] == "192" and tracks[0]["direction"] == 0
    assert tracks[0]["track"] == "121.42,31.13;121.43,31.14"


def test_parse_missing_or_empty():
    assert parse_line_tracks({}) == []
    assert parse_line_tracks({"Data": {"LineName": "x", "UpObject": {"UpDown": 0, "LineLonLat": ""}}}) == []
    assert parse_line_tracks("not-a-dict") == []


def test_parse_skips_direction_without_updown():
    # a direction object missing UpDown must be skipped (direction is NOT NULL), not emitted as None
    raw = {"Data": {"LineName": "9", "NorCode": "000900",
                    "UpObject": {"LineLonLat": "1,2;3,4"},          # no UpDown -> skipped
                    "DownObject": {"UpDown": 1, "LineLonLat": "3,4;1,2"}}}
    tracks = parse_line_tracks(raw)
    assert [t["direction"] for t in tracks] == [1]
