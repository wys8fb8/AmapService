# amap_service/paths.py
"""应用基准目录与外置文件路径解析。

冻结(PyInstaller)运行时，基准目录取 exe 所在目录，使 exe 旁边的 config\\、
SQLite DB 等外置文件不依赖当前工作目录(cwd)即可稳定定位；源码运行时维持
相对 cwd 的旧行为，对开发与测试零影响。
"""
import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    """冻结时 = exe 所在目录；源码运行时 = 当前工作目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def default_config_path() -> Path:
    """默认配置文件路径：<基准目录>/config/config.yaml。"""
    return app_base_dir() / "config" / "config.yaml"


def resolve_data_path(path: str) -> str:
    """把数据文件路径锚定到基准目录。

    - `:memory:`(SQLite 内存库)与绝对路径原样返回；
    - 相对路径相对基准目录解析，返回字符串。
    """
    if path == ":memory:" or os.path.isabs(path):
        return path
    return str(app_base_dir() / path)
