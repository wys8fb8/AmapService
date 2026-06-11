# tests/test_paths.py
from pathlib import Path
import sys
import amap_service.paths as paths


def test_base_dir_source_mode_is_cwd(monkeypatch):
    """非冻结(源码运行)时基准目录 = 当前工作目录。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths.app_base_dir() == Path.cwd()


def test_base_dir_frozen_is_executable_dir(monkeypatch, tmp_path):
    """冻结(PyInstaller)时基准目录 = exe 所在目录。"""
    exe = tmp_path / "amap-service.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    assert paths.app_base_dir() == tmp_path


def test_default_config_path_is_under_base(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"), raising=False)
    assert paths.default_config_path() == tmp_path / "config" / "config.yaml"


def test_resolve_data_path_relative_anchors_to_base(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "x.exe"), raising=False)
    assert paths.resolve_data_path("road_network.db") == str(tmp_path / "road_network.db")


def test_resolve_data_path_absolute_unchanged(tmp_path):
    abs_p = str(tmp_path / "abs.db")
    assert paths.resolve_data_path(abs_p) == abs_p


def test_resolve_data_path_memory_unchanged():
    assert paths.resolve_data_path(":memory:") == ":memory:"
