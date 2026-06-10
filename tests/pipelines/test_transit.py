import json
import httpx
from sqlalchemy import func, select
from amap_service.config.schema import AppConfig, DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_line_raw
from amap_service.clients.transit import TransitClient
from amap_service.pipelines.transit import run_transit_stage1


def _config(token_path="data.token", line_name_path="data", line_name_field=None, line_limit=0):
    return AppConfig.model_validate({
        "amap": {"endpoint": "http://h", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "yangs", "password": "pw",
                    "token_url": "http://h/token", "line_list_url": "http://h/list",
                    "line_entity_url": "http://h/entity",
                    "token_path": token_path, "line_name_path": line_name_path,
                    "line_name_field": line_name_field, "line_limit": line_limit},
    })


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client(handler):
    return TransitClient(_config().transit, transport=httpx.MockTransport(handler), now_ms=lambda: 1)


def test_full_chain_archives_token_list_entities(tmp_path):
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"lineName": "L1"}, {"lineName": "L2"}]})
        return httpx.Response(200, text=json.dumps({"line": request.url.params.get("lineName")}))

    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 111)
    assert stats == {"token_ok": True, "line_count": 2, "entities_archived": 2}
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 4
    written = {p.name for p in out.iterdir()}
    assert "token_111.json" in written and "line_list_111.json" in written
    assert "line_entity_L1_111.json" in written and "line_entity_L2_111.json" in written


def test_line_limit_caps_entity_fetches(tmp_path):
    fetched = []
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [
                {"Roadline": "1"}, {"Roadline": "2"}, {"Roadline": "3"}, {"Roadline": "4"}]})
        fetched.append(request.url.params.get("lineName"))
        return httpx.Response(200, text="{}")

    e = _engine(tmp_path)
    cfg = _config(line_name_path="data", line_name_field="Roadline", line_limit=2)
    stats = run_transit_stage1(e, _client(handler), cfg, out_dir=str(tmp_path / "raw"), now_ms=lambda: 1)
    assert stats["line_count"] == 4 and stats["entities_archived"] == 2   # discovered 4, fetched 2
    assert fetched == ["1", "2"]


def test_degrade_when_token_not_extracted(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(token_path="data.token"),
                               out_dir=str(out), now_ms=lambda: 5)
    assert stats == {"token_ok": False, "line_count": 0, "entities_archived": 0}
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 1


def test_degrade_when_no_line_names(tmp_path):
    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        return httpx.Response(200, json={"data": []})
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 7)
    assert stats == {"token_ok": True, "line_count": 0, "entities_archived": 0}
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 2


def test_entity_failure_isolation_skips_and_continues(tmp_path):
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"lineName": "BAD"}, {"lineName": "OK"}]})
        return httpx.Response(200, text="{}")
    e = _engine(tmp_path)
    client = _client(handler)
    orig = client.get_line_entity
    def ge(token, name):
        if name == "BAD":
            raise RuntimeError("boom")
        return orig(token, name)
    client.get_line_entity = ge
    stats = run_transit_stage1(e, client, _config(), out_dir=str(tmp_path / "raw"), now_ms=lambda: 1)
    assert stats["line_count"] == 2 and stats["entities_archived"] == 1   # BAD skipped, OK archived


def test_entity_archived_with_bare_line_name_in_db(tmp_path):
    """transit_line_raw.line_name 存裸线路号（"47"），磁盘文件名仍带 line_entity_ 前缀。"""
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"lineName": "47"}]})
        return httpx.Response(200, text=json.dumps({"Data": {"LineName": "47"}}))
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 1)
    with e.connect() as c:
        names = [r[0] for r in c.execute(select(transit_line_raw.c.line_name)).all()]
    assert "47" in names                    # DB 存裸线路号
    assert "line_entity_47" not in names    # 不再把文件前缀塞进 DB
    assert (out / "line_entity_47_1.json").exists()   # 磁盘文件仍带前缀


def test_long_line_name_does_not_crash(tmp_path):
    longname = "路" * 300
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"lineName": longname}]})
        return httpx.Response(200, text="{}")
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 9)
    assert stats["entities_archived"] == 1
    with e.connect() as c:
        names = [r[0] for r in c.execute(select(transit_line_raw.c.line_name)).all()]
    assert any(longname in n for n in names)        # full name preserved in DB
