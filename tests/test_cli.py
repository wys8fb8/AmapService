import textwrap
from sqlalchemy import inspect
import amap_service.cli as cli
from amap_service.db.engine import make_engine
from amap_service.config.loader import load_config

CONFIG_TMPL = """
amap:
  endpoint: "http://192.168.102.102:8080"
  jobs:
    road_network: {{path: "/road", cron: "0 1 * * *"}}
    traffic_status: {{path: "/traffic", cron: "*/2 * * * *"}}
transit:
  username: u
  password: p
  token_url: http://t
  line_list_url: http://l
  line_entity_url: http://e
database:
  type: sqlite
  sqlite: {{path: "{db}"}}
"""


def _write_config(tmp_path):
    db = tmp_path / "road.db"
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(CONFIG_TMPL.format(db=str(db))), encoding="utf-8")
    return p, db


def test_cmd_initdb_creates_tables(tmp_path):
    cfg_path, db = _write_config(tmp_path)
    cli.main(["initdb", "-c", str(cfg_path)])
    engine = make_engine(load_config(cfg_path).database)
    tables = set(inspect(engine).get_table_names())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= tables


def test_run_once_dispatches_road_network(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    calls = {}
    def fake_rn(engine, client, endpoint, path, parse_mode):
        calls.update(endpoint=endpoint, path=path, parse_mode=parse_mode)
        return {"inserted": 0}
    monkeypatch.setattr(cli, "run_road_network", fake_rn)
    cli.main(["run-once", "road-network", "-c", str(cfg_path)])
    assert calls == {"endpoint": "http://192.168.102.102:8080", "path": "/road", "parse_mode": "memory"}


def test_run_once_dispatches_traffic(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_traffic(engine, client, endpoint, path, parse_mode, cache=None, snapshot=False,
                     incremental=False, **_):
        seen.update(path=path, has_cache=cache is not None)
        return {"inserted": 0}
    monkeypatch.setattr(cli, "run_traffic", fake_traffic)
    cli.main(["run-once", "traffic", "-c", str(cfg_path)])
    assert seen["path"] == "/traffic" and seen["has_cache"] is True


def test_run_once_dispatches_transit_build(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_build(engine, transit_client, config, **kw):
        seen["called"] = True
        seen["has_client"] = transit_client is not None
        return {"lines": 0, "segments": 0}
    monkeypatch.setattr(cli, "run_transit_build", fake_build)
    cli.main(["run-once", "transit-build", "-c", str(cfg_path)])
    assert seen == {"called": True, "has_client": True}


def test_run_builds_and_starts_scheduler(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    started = {"n": 0}
    class FakeSched:
        def get_jobs(self): return []
        def start(self): started["n"] += 1
    monkeypatch.setattr(cli, "build_scheduler", lambda *a, **k: FakeSched())
    cli.main(["run", "-c", str(cfg_path)])
    assert started["n"] == 1


def test_run_once_dispatches_transit(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_stage1(engine, transit_client, config, **kw):
        seen["called"] = True
        seen["has_client"] = transit_client is not None
        return {"token_ok": False, "line_count": 0, "entities_archived": 0}
    monkeypatch.setattr(cli, "run_transit_stage1", fake_stage1)
    cli.main(["run-once", "transit", "-c", str(cfg_path)])
    assert seen == {"called": True, "has_client": True}


def test_run_once_section_build_dispatches(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_section_build(engine, config):
        seen["called"] = True
        seen["has_engine"] = engine is not None
        return {"segments": 0}
    monkeypatch.setattr(cli, "run_section_build", fake_section_build)
    cli.main(["run-once", "section-build", "-c", str(cfg_path)])
    assert seen == {"called": True, "has_engine": True}


def test_serve_refuses_when_api_disabled(tmp_path, monkeypatch):
    """api.enabled=false 时 serve 拒绝启动。"""
    import pytest
    from amap_service import cli
    from amap_service.config.schema import AppConfig

    raw = {
        "amap": {"endpoint": "http://x", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "u", "password": "p", "token_url": "http://a",
                    "line_list_url": "http://b", "line_entity_url": "http://c"},
        "api": {"enabled": False},
    }
    monkeypatch.setattr(cli, "load_config", lambda p: AppConfig.model_validate(raw))
    with pytest.raises(SystemExit):
        cli.cmd_serve("dummy.yaml")


def test_serve_starts_uvicorn_when_enabled(monkeypatch):
    from amap_service import cli
    from amap_service.config.schema import AppConfig

    raw = {
        "amap": {"endpoint": "http://x", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "u", "password": "p", "token_url": "http://a",
                    "line_list_url": "http://b", "line_entity_url": "http://c"},
        "database": {"type": "sqlite", "sqlite": {"path": ":memory:"}},
        "api": {"enabled": True, "host": "1.2.3.4", "port": 9999},
    }
    monkeypatch.setattr(cli, "load_config", lambda p: AppConfig.model_validate(raw))
    called = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port: called.update(host=host, port=port))
    cli.cmd_serve("dummy.yaml")
    assert called == {"host": "1.2.3.4", "port": 9999}
