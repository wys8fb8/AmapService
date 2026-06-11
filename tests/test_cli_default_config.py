# tests/test_cli_default_config.py
import amap_service.cli as cli
import amap_service.paths as paths


def test_default_config_uses_paths_helper(monkeypatch, tmp_path):
    """不传 -c 时，cmd_initdb 收到的路径来自 paths.default_config_path()。"""
    sentinel = tmp_path / "sentinel" / "config.yaml"
    monkeypatch.setattr(paths, "default_config_path", lambda: sentinel)
    seen = {}
    monkeypatch.setattr(cli, "cmd_initdb", lambda config_path: seen.update(path=config_path))
    cli.main(["initdb"])
    assert seen["path"] == str(sentinel)


def test_explicit_config_overrides_default(monkeypatch):
    """显式 -c 时以用户传入为准。"""
    seen = {}
    monkeypatch.setattr(cli, "cmd_initdb", lambda config_path: seen.update(path=config_path))
    cli.main(["initdb", "-c", "/tmp/custom.yaml"])
    assert seen["path"] == "/tmp/custom.yaml"
