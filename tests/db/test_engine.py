import sys
from pathlib import Path
from sqlalchemy import text
from amap_service.config.schema import DatabaseConfig, SqliteConfig, MysqlConfig
from amap_service.db.engine import build_url, make_engine


def test_sqlite_url_relative_anchored(monkeypatch, tmp_path):
    """Relative path is anchored to exe dir when frozen, or cwd otherwise."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"), raising=False)
    cfg = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path="road_network.db"))
    assert build_url(cfg) == f"sqlite:///{tmp_path / 'road_network.db'}"

def test_mysql_url():
    cfg = DatabaseConfig(
        type="mysql",
        mysql=MysqlConfig(user="u", password="p", host="h", port=3307, database="d", charset="utf8mb4"),
    )
    assert build_url(cfg) == "mysql+pymysql://u:p@h:3307/d?charset=utf8mb4"

def test_sqlite_engine_connects(tmp_path):
    db = tmp_path / "t.db"
    cfg = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(db)))
    engine = make_engine(cfg)
    with engine.connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1


def test_sqlite_pragmas_applied(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    with e.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert int(c.execute(text("PRAGMA synchronous")).scalar()) == 1   # NORMAL
