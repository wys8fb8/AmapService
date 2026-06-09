from amap_service.parsing.transit import extract_token, extract_line_names


def test_extract_token_explicit_path():
    raw = {"data": {"token": "T123"}}
    assert extract_token(raw, "data.token") == "T123"


def test_extract_token_heuristic_top_level():
    assert extract_token({"token": "X"}) == "X"
    assert extract_token({"accessToken": "Y"}) == "Y"


def test_extract_token_heuristic_nested():
    assert extract_token({"data": {"accessToken": "Z"}}) == "Z"
    assert extract_token({"result": {"token": "R"}}) == "R"


def test_extract_token_missing_returns_none():
    assert extract_token({"nope": 1}) is None
    assert extract_token({"data": {"token": "T"}}, "data.missing") is None
    assert extract_token("not-a-dict") is None


def test_extract_line_names_explicit_path_list_of_dicts():
    raw = {"data": [{"lineName": "L1"}, {"lineName": "L2"}]}
    assert extract_line_names(raw, "data") == ["L1", "L2"]


def test_extract_line_names_heuristic_list_of_str():
    assert extract_line_names(["A", "B"]) == ["A", "B"]


def test_extract_line_names_heuristic_container():
    assert extract_line_names({"data": [{"name": "N1"}, {"lineName": "N2"}]}) == ["N1", "N2"]


def test_extract_line_names_with_name_field():
    # real 久事 shape: {"Data": [{"Roadline": "47", ...}, ...]}
    raw = {"Data": [{"Roadline": "47", "Company": "A"}, {"Roadline": "新区2", "Company": "B"}]}
    assert extract_line_names(raw, "Data", "Roadline") == ["47", "新区2"]


def test_extract_line_names_roadline_heuristic():
    raw = {"Data": [{"Roadline": "47"}, {"Normalcode": "004700"}]}
    assert extract_line_names(raw) == ["47", "004700"]


def test_extract_line_names_name_field_skips_missing():
    raw = {"Data": [{"Roadline": "47"}, {"other": "x"}, {"Roadline": ""}]}
    assert extract_line_names(raw, "Data", "Roadline") == ["47"]


def test_extract_line_names_undetermined_empty():
    assert extract_line_names({"weird": 1}) == []
    assert extract_line_names({"data": [{"id": 1}]}, name_field="Roadline") == []


from amap_service.parsing.transit import parse_line_stations


def test_parse_line_stations_both_directions():
    raw = {"Data": {"LineName": "192", "NorCode": "019200",
                    "UpObject": {"UpDown": 0, "Stations": [
                        {"LevelId": 1, "LevelName": "A", "Lon02": 121.42, "Lat02": 31.13},
                        {"LevelId": 2, "LevelName": "B", "Lon02": 121.44, "Lat02": 31.16}]},
                    "DownObject": {"UpDown": 1, "Stations": [
                        {"LevelId": 1, "LevelName": "C", "Lon02": 121.5, "Lat02": 31.2}]}}}
    out = parse_line_stations(raw)
    assert [(d["line_name"], d["direction"], len(d["stations"])) for d in out] == [
        ("192", 0, 2), ("192", 1, 1)]
    assert out[0]["nor_code"] == "019200"
    assert out[0]["stations"][0] == {"level_id": 1, "level_name": "A",
                                     "longitude": 121.42, "latitude": 31.13}


def test_parse_line_stations_skips_incomplete_and_missing_dir():
    raw = {"Data": {"LineName": "X", "UpObject": {"Stations": [
        {"LevelId": 1, "LevelName": "A", "Lon02": None, "Lat02": 31.1},   # 缺坐标 -> 丢
        {"LevelId": 2, "LevelName": "B", "Lon02": 121.4, "Lat02": 31.1}]},
        "DownObject": None}}
    out = parse_line_stations(raw)
    assert len(out) == 1 and out[0]["direction"] == 0
    assert [s["level_id"] for s in out[0]["stations"]] == [2]


def test_parse_line_stations_empty():
    assert parse_line_stations({"Data": {}}) == []
    assert parse_line_stations({}) == []
