# tests/test_packaging_deps.py
"""守护测试：amap-service.spec 里声明要收集/隐藏导入的依赖，必须在当前环境真的能导入。
任何一个 import 失败 => 打出来的 exe 必然在运行期 ModuleNotFoundError。"""
import importlib
import pytest

REQUIRED = [
    "uvicorn",
    "websockets",
    "httptools",
    "fastapi",
    "pydantic",
    "pydantic_core",
    "google.protobuf",
    "ijson",
    "apscheduler.triggers.cron",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.mysql",
    "anyio._backends._asyncio",
    "paho.mqtt.client",
    "redis",
    "openpyxl",
    "amap_service.publish.proto.line_traffic_pb2",
]


@pytest.mark.parametrize("mod", REQUIRED)
def test_packaging_dependency_importable(mod):
    importlib.import_module(mod)
