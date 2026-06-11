# amap-service.spec
# PyInstaller onedir 打包脚本。构建: pyinstaller amap-service.spec --noconfirm
# 产物: dist/amap-service/ (amap-service.exe + _internal/)
# 需要 PyInstaller >= 6 (onedir COLLECT 不含 a.zipfiles)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# 含二进制扩展 / 运行期动态导入，必须整包收集
for pkg in ("pydantic", "pydantic_core", "google.protobuf", "ijson", "websockets"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 子模块按名动态加载
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("openpyxl")
hiddenimports += [
    "anyio._backends._asyncio",  # anyio 运行期按字符串动态 import 后端
    "httptools",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "apscheduler.triggers.cron",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.mysql",  # repositories.py 静态导入;方言动态加载
    "pymysql",  # 用 MySQL 时需要;未装也不致命(运行期才 import)
    "paho.mqtt.client",
    "redis",
]

# 非代码资源(_pb2.py 是代码会自动收;.proto 仅作契约文档随包)
datas += [
    ("config/config.yaml.example", "config"),
    ("proto/line_traffic.proto", "proto"),
]

a = Analysis(
    ["amap_service/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "fakeredis", "grpc_tools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir: 二进制放 _internal/
    name="amap-service",
    console=True,            # 服务程序需要 stdout 日志
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="amap-service",
)
