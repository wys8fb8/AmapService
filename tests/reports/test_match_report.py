import json

from sqlalchemy import insert

from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_segment, transit_line_raw
from amap_service.sdk import geometry
from amap_service.reports.match_report import build_match_report, write_match_report_csv


def _eng(tmp_path):
    eng = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(eng)
    return eng


def _plen(s):
    pts = geometry.parse_track(s)
    return sum(geometry.haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def test_build_match_report_computes_lengths_and_diff(tmp_path):
    eng = _eng(tmp_path)
    seg0 = "121.0,31.0;121.001,31.0"
    seg1 = "121.001,31.0;121.002,31.0"
    orig = "121.0,31.0;121.0005,31.0005;121.001,31.0;121.002,31.0"  # 带绕行,比匹配后长
    with eng.begin() as c:
        c.execute(insert(transit_segment), [
            {"line_name": "47", "direction": 0, "seq": 0, "link_id": 1,
             "reverse_coords": 0, "line_track": seg0},
            {"line_name": "47", "direction": 0, "seq": 1, "link_id": 2,
             "reverse_coords": 0, "line_track": seg1}])
        c.execute(insert(transit_line_raw), [
            {"line_name": "47", "raw_response": json.dumps(
                {"Data": {"LineName": "47", "UpObject": {"UpDown": 0, "LineLonLat": orig}}})}])
    rows = build_match_report(eng)
    assert len(rows) == 1
    r = rows[0]
    assert r["line_name"] == "47"
    assert r["direction"] == 0 and r["direction_label"] == "上行"
    matched, original = _plen(seg0) + _plen(seg1), _plen(orig)
    assert abs(r["matched_len_m"] - round(matched, 1)) < 0.05
    assert abs(r["original_len_m"] - round(original, 1)) < 0.05
    assert r["diff_pct"] == round((matched - original) / original * 100, 2)


def test_missing_original_yields_none(tmp_path):
    eng = _eng(tmp_path)
    with eng.begin() as c:
        c.execute(insert(transit_segment), [
            {"line_name": "99", "direction": 1, "seq": 0, "link_id": 1,
             "reverse_coords": 0, "line_track": "121.0,31.0;121.001,31.0"}])
    rows = build_match_report(eng)
    assert rows[0]["direction_label"] == "下行"
    assert rows[0]["original_len_m"] is None
    assert rows[0]["diff_pct"] is None


def test_latest_raw_per_line_used(tmp_path):
    eng = _eng(tmp_path)
    with eng.begin() as c:
        c.execute(insert(transit_segment), [
            {"line_name": "47", "direction": 0, "seq": 0, "link_id": 1,
             "reverse_coords": 0, "line_track": "121.0,31.0;121.001,31.0"}])
        # 旧响应(短) + 新响应(长) → 取最新(max id)
        c.execute(insert(transit_line_raw), [
            {"line_name": "47", "raw_response": json.dumps(
                {"Data": {"LineName": "47", "UpObject": {"UpDown": 0, "LineLonLat": "121.0,31.0;121.0005,31.0"}}})},
            {"line_name": "47", "raw_response": json.dumps(
                {"Data": {"LineName": "47", "UpObject": {"UpDown": 0, "LineLonLat": "121.0,31.0;121.002,31.0"}}})}])
    rows = build_match_report(eng)
    assert abs(rows[0]["original_len_m"] - round(_plen("121.0,31.0;121.002,31.0"), 1)) < 0.05


def test_write_csv_headers_and_values(tmp_path):
    rows = [{"line_name": "47", "direction": 0, "direction_label": "上行",
             "original_len_m": 100.0, "matched_len_m": 110.0, "diff_pct": 10.0},
            {"line_name": "99", "direction": 1, "direction_label": "下行",
             "original_len_m": None, "matched_len_m": 50.0, "diff_pct": None}]
    out = tmp_path / "r.csv"
    write_match_report_csv(rows, str(out))
    text = out.read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "线路名称,上下行,原始轨迹长度,匹配后长度,差异百分比"
    assert lines[1] == "47,上行,100.0,110.0,10.0"
    assert lines[2] == "99,下行,,50.0,"   # 原始/差异缺失留空
