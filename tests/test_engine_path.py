import sys
from amap_service.db.engine import build_url
from amap_service.config.schema import DatabaseConfig, SqliteConfig


def _db(path):
    return DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=path))


def test_build_url_relative_anchored(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"), raising=False)
    url = build_url(_db("road_network.db"))
    assert url == f"sqlite:///{tmp_path / 'road_network.db'}"


def test_build_url_absolute_unchanged(tmp_path):
    abs_p = str(tmp_path / "abs.db")
    assert build_url(_db(abs_p)) == f"sqlite:///{abs_p}"


def test_build_url_memory_unchanged():
    assert build_url(_db(":memory:")) == "sqlite:///:memory:"
