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
