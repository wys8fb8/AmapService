from sqlalchemy import insert

from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import (
    road_link, transit_segment, transit_section_link, transit_station,
)
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.views.static_cache import StaticLineCache


def _engine(tmp_path):
    db = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db")))
    eng = make_engine(db)
    init_db(eng)
    return eng


def _seed(eng):
    with eng.begin() as c:
        c.execute(insert(road_link), [
            {"link_id": 5130091959790075998, "line_track": "121.1,31.1;121.2,31.2"}])
        c.execute(insert(transit_segment), [
            {"line_name": "47", "direction": 0, "seq": 0,
             "link_id": 5130091959790075998, "reverse_coords": 1,
             "line_track": "121.1,31.1;121.2,31.2"}])
        c.execute(insert(transit_section_link), [
            {"line_name": "47", "direction": 0, "from_level_id": 1, "to_level_id": 2,
             "seq": 0, "link_id": 5130091959790075998, "length_m": 100.0, "pct": 100}])
        c.execute(insert(transit_station), [
            {"line_name": "47", "direction": 0, "level_id": 1,
             "longitude": 121.1, "latitude": 31.1},
            {"line_name": "47", "direction": 0, "level_id": 2,
             "longitude": 121.2, "latitude": 31.2}])


def test_segments_and_sections_loaded(tmp_path):
    eng = _engine(tmp_path)
    _seed(eng)
    cache = StaticLineCache(eng)
    segs = cache.segments("47")
    assert list(segs.keys()) == [0]
    assert segs[0][0]["link_id"] == 5130091959790075998
    assert segs[0][0]["reverse"] == 1
    secs = cache.sections("47")
    assert secs[0][0]["from_level_id"] == 1
    assert secs[0][0]["to_level_id"] == 2
    assert secs[0][0]["links"][0]["pct"] == 100
    assert cache.link_track(5130091959790075998) == "121.1,31.1;121.2,31.2"


def test_lines_index(tmp_path):
    eng = _engine(tmp_path)
    _seed(eng)
    cache = StaticLineCache(eng)
    lines = cache.lines()
    assert len(lines) == 1
    entry = lines[0]
    assert entry["line_name"] == "47"
    assert entry["directions"] == [0]
    assert entry["has_segments"] is True
    assert entry["has_sections"] is True
    assert entry["station_count"] == 2


def test_unknown_line_empty(tmp_path):
    eng = _engine(tmp_path)
    _seed(eng)
    cache = StaticLineCache(eng)
    assert cache.segments("999") == {}
    assert cache.sections("999") == {}


def test_link_tracks_chunked_beyond_in_limit(tmp_path, monkeypatch):
    # 回归:link_id 数量超过单条 IN(...) 的拆批阈值时,_reload 必须分批查询,
    # 不能把全部 id 塞进一条 IN 而触发 "too many SQL variables"。
    import amap_service.views.static_cache as sc
    monkeypatch.setattr(sc, "_IN_CHUNK", 100)
    n = 250  # > 2*_IN_CHUNK,确保至少 3 批
    base = 5130091959790075998
    eng = _engine(tmp_path)
    with eng.begin() as c:
        c.execute(insert(road_link), [
            {"link_id": base + i, "line_track": f"121.{i},31.{i}"} for i in range(n)])
        c.execute(insert(transit_section_link), [
            {"line_name": "47", "direction": 0, "from_level_id": i, "to_level_id": i + 1,
             "seq": i, "link_id": base + i, "length_m": 1.0, "pct": 100} for i in range(n)])
    cache = sc.StaticLineCache(eng)
    assert len(cache.sections("47")[0]) == n
    # 每个 link 的 line_track 都被分批查回,无遗漏。
    for i in range(n):
        assert cache.link_track(base + i) == f"121.{i},31.{i}"


def test_reload_on_version_change(tmp_path):
    eng = _engine(tmp_path)
    _seed(eng)
    cache = StaticLineCache(eng, ttl_seconds=0)
    assert "192" not in {l["line_name"] for l in cache.lines()}
    with eng.begin() as c:
        c.execute(insert(transit_segment), [
            {"line_name": "192", "direction": 0, "seq": 0,
             "link_id": 5130091959790075998, "reverse_coords": 0,
             "line_track": "121.1,31.1;121.2,31.2"}])
    assert "192" in {l["line_name"] for l in cache.lines()}
