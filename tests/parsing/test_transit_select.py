from amap_service.parsing.transit import extract_line_records, select_line_names

# Real 久事 line-list shape: {"Data": [{"Roadline","Company",...}]}
RAW = {"Data": [
    {"Roadline": "47", "Company": "巴士一公司"},
    {"Roadline": "192", "Company": "巴士二公司"},
    {"Roadline": "300", "Company": "浦东公司"},
    {"Roadline": "66", "Company": "巴士一公司"},
]}


def test_extract_line_records_carries_company():
    recs = extract_line_records(RAW, "Data", "Roadline", "Company")
    assert recs == [
        {"name": "47", "company": "巴士一公司"},
        {"name": "192", "company": "巴士二公司"},
        {"name": "300", "company": "浦东公司"},
        {"name": "66", "company": "巴士一公司"},
    ]


def test_select_no_filters_returns_all_in_order():
    recs = extract_line_records(RAW, "Data", "Roadline")
    assert select_line_names(recs) == ["47", "192", "300", "66"]


def test_select_by_company():
    recs = extract_line_records(RAW, "Data", "Roadline")
    got = select_line_names(recs, companys={"巴士一公司", "巴士二公司"})
    assert got == ["47", "192", "66"]   # 300 (浦东公司) excluded


def test_select_specific_lines():
    recs = extract_line_records(RAW, "Data", "Roadline")
    assert select_line_names(recs, lines={"192", "66"}) == ["192", "66"]


def test_select_company_then_lines_then_limit():
    recs = extract_line_records(RAW, "Data", "Roadline")
    # company keeps 47,192,66 ; lines narrows to 47,66 ; limit caps to 1
    got = select_line_names(recs, companys={"巴士一公司", "巴士二公司"},
                            lines={"47", "66"}, limit=1)
    assert got == ["47"]


def test_select_limit_only():
    recs = extract_line_records(RAW, "Data", "Roadline")
    assert select_line_names(recs, limit=2) == ["47", "192"]
