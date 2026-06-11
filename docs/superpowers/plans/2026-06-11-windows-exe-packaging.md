# Windows EXE 打包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `amap-service` 打成一个 Windows onedir 可执行文件，子命令区分 run/serve 两个常驻进程，配置文件外置，改配置不用重打包。

**Architecture:** 新增一个跨平台的路径锚定模块 `amap_service/paths.py`，让冻结后的 exe 稳定从自身目录找 `config\config.yaml` 和相对 SQLite DB；`cli.py` 与 `db/engine.py` 各接一处。打包用 PyInstaller 的 `amap-service.spec`（onedir，显式声明 uvicorn/pydantic/protobuf/ijson 等动态导入）。Tasks 1–3 用 pytest 在当前 Mac 上即可验证；Task 4–6 产出打包脚本/批处理/文档；Task 7 是在 Windows 机器上的构建与实跑验收。

**Tech Stack:** Python 3.11、PyInstaller（onedir）、pytest、PyYAML、SQLAlchemy、FastAPI/uvicorn、protobuf。

参考规格：[docs/superpowers/specs/2026-06-11-windows-exe-packaging-design.md](../specs/2026-06-11-windows-exe-packaging-design.md)

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `amap_service/paths.py` | 应用基准目录解析（冻结=exe 目录，源码=cwd）；默认配置路径；相对数据路径锚定 | Create |
| `tests/test_paths.py` | 验证 paths 在 frozen / 源码两种情形的行为 | Create |
| `amap_service/cli.py` | argparse 默认 `-c` 改用 `paths.default_config_path()` | Modify |
| `tests/test_cli_default_config.py` | 验证不传 `-c` 时用默认配置路径 | Create |
| `amap_service/db/engine.py` | SQLite 相对 path 经 `paths.resolve_data_path` 锚定 | Modify |
| `tests/test_engine_path.py` | 验证 build_url 对相对/绝对/`:memory:` 的处理 | Create |
| `tests/test_packaging_deps.py` | 守护测试：确认要打包的动态依赖在当前环境可导入 | Create |
| `amap-service.spec` | PyInstaller 打包脚本（onedir + 全部 hidden imports / datas） | Create |
| `启动-服务.bat` / `启动-接口.bat` | 便捷启动脚本 | Create |
| `docs/打包说明.md` | Windows 构建步骤、目录布局、ImportError 排查清单 | Create |

---

## Task 1: 路径锚定模块 `amap_service/paths.py`

**Files:**
- Create: `amap_service/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'amap_service.paths'`）

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paths.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add amap_service/paths.py tests/test_paths.py
git commit -m "feat(paths): 冻结后锚定 exe 目录的路径解析模块"
```

---

## Task 2: cli.py 默认配置路径接入 paths

**Files:**
- Modify: `amap_service/cli.py`（argparse 中 `default="config/config.yaml"` 共 4 处）
- Test: `tests/test_cli_default_config.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_default_config.py -v`
Expected: FAIL（默认值仍是字符串 `"config/config.yaml"`，第一个断言不等）

- [ ] **Step 3: Modify cli.py**

在 `amap_service/cli.py` 顶部 import 区加：

```python
from amap_service import paths
```

把 `main()` 里 argparse 的默认值改为运行时求值（不能直接把 `default=paths.default_config_path()` 写死，否则 import 期就固定；用 `str(...)` 在 `main()` 执行时取值即可）。将这两处：

```python
    for name in ("initdb", "run", "serve"):
        sp = sub.add_parser(name)
        sp.add_argument("-c", "--config", default="config/config.yaml")
    ro = sub.add_parser("run-once")
    ro.add_argument("job", choices=["road-network", "traffic", "transit", "transit-build", "section-build"])
    ro.add_argument("-c", "--config", default="config/config.yaml")
    mr = sub.add_parser("match-report")
    mr.add_argument("-c", "--config", default="config/config.yaml")
```

改为：

```python
    default_config = str(paths.default_config_path())
    for name in ("initdb", "run", "serve"):
        sp = sub.add_parser(name)
        sp.add_argument("-c", "--config", default=default_config)
    ro = sub.add_parser("run-once")
    ro.add_argument("job", choices=["road-network", "traffic", "transit", "transit-build", "section-build"])
    ro.add_argument("-c", "--config", default=default_config)
    mr = sub.add_parser("match-report")
    mr.add_argument("-c", "--config", default=default_config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_default_config.py tests/test_cli.py -v`
Expected: PASS（新测试 2 passed；原 test_cli.py 全绿，因其均显式传 `-c`）

- [ ] **Step 5: Commit**

```bash
git add amap_service/cli.py tests/test_cli_default_config.py
git commit -m "feat(cli): 默认配置路径走 paths.default_config_path()"
```

---

## Task 3: db/engine.py 锚定 SQLite 相对路径

**Files:**
- Modify: `amap_service/db/engine.py`（`build_url` 的 sqlite 分支）
- Test: `tests/test_engine_path.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_path.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_path.py -v`
Expected: FAIL（`test_build_url_relative_anchored`：当前 build_url 不锚定，返回 `sqlite:///road_network.db`）

- [ ] **Step 3: Modify engine.py**

在 `amap_service/db/engine.py` 顶部 import 区加：

```python
from amap_service.paths import resolve_data_path
```

把 `build_url` 的 sqlite 分支：

```python
    if db.type == "sqlite":
        return f"sqlite:///{db.sqlite.path}"
```

改为：

```python
    if db.type == "sqlite":
        return f"sqlite:///{resolve_data_path(db.sqlite.path)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine_path.py tests/test_cli.py -v`
Expected: PASS（新测试 3 passed；原 test_cli.py 用绝对 tmp_path / `:memory:`，不受影响）

- [ ] **Step 5: Commit**

```bash
git add amap_service/db/engine.py tests/test_engine_path.py
git commit -m "feat(db): SQLite 相对 path 锚定 exe 目录"
```

---

## Task 4: PyInstaller 打包脚本 `amap-service.spec`

**Files:**
- Create: `amap-service.spec`
- Test: `tests/test_packaging_deps.py`（守护测试：确认要打包的动态依赖在当前环境都能 import；打包前先抓漏）

- [ ] **Step 1: Write the failing test**

```python
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
    "paho.mqtt.client",
    "redis",
    "openpyxl",
    "amap_service.publish.proto.line_traffic_pb2",
]


@pytest.mark.parametrize("mod", REQUIRED)
def test_packaging_dependency_importable(mod):
    importlib.import_module(mod)
```

- [ ] **Step 2: Run test to verify it fails (or reveals gaps)**

Run: `pytest tests/test_packaging_deps.py -v`
Expected: 若开发环境未装 `uvicorn[standard]` 的 `websockets`/`httptools`，会有用例 FAIL。先 `pip install -e ".[dev]"` 补齐依赖，直到全 PASS——这正是这条测试的目的（打包前暴露缺失依赖）。

- [ ] **Step 3: Create the spec file**

```python
# amap-service.spec
# PyInstaller onedir 打包脚本。构建: pyinstaller amap-service.spec --noconfirm
# 产物: dist/amap-service/ (amap-service.exe + _internal/)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# 含二进制扩展 / 运行期动态导入，必须整包收集
for pkg in ("pydantic", "pydantic_core", "google.protobuf", "ijson"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 子模块按名动态加载
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("openpyxl")
hiddenimports += [
    "websockets",
    "httptools",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "apscheduler.triggers.cron",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
    "sqlalchemy.dialects.sqlite",
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
```

- [ ] **Step 4: Verify the spec parses (syntax check)**

> 说明：真正的 `pyinstaller` 构建在 Windows 机器上做（Task 7）。这里只做 Python 语法自检，确保 spec 无语法错误。

Run: `python -c "compile(open('amap-service.spec').read(), 'amap-service.spec', 'exec'); print('spec syntax ok')"`
Expected: 输出 `spec syntax ok`（注意：`Analysis`/`PYZ` 等是 PyInstaller 注入的全局名，compile 阶段不报错；不要直接 `exec`）

- [ ] **Step 5: Commit**

```bash
git add amap-service.spec tests/test_packaging_deps.py
git commit -m "build(pyinstaller): onedir 打包 spec + 依赖守护测试"
```

---

## Task 5: 便捷启动批处理脚本

**Files:**
- Create: `启动-服务.bat`
- Create: `启动-接口.bat`

> 这两个脚本会随源码提交，并在 Task 7 构建后手工拷进 `dist/amap-service/`（或在 spec 的 datas 里带上——但 datas 会落到 exe 同级，路径合适，可选）。此处仅产出脚本本体；无自动化测试（纯批处理），验收靠 Task 7 实跑。

- [ ] **Step 1: Create `启动-服务.bat`**

```bat
@echo off
REM 启动 cron 守护进程(拉数据落库 + MQTT 发布)。配置见 config\config.yaml。
cd /d "%~dp0"
amap-service.exe run -c config\config.yaml
pause
```

- [ ] **Step 2: Create `启动-接口.bat`**

```bat
@echo off
REM 启动 HTTP API 进程(需 config 中 api.enabled: true)。Swagger: http://<host>:<port>/docs
cd /d "%~dp0"
amap-service.exe serve -c config\config.yaml
pause
```

- [ ] **Step 3: Commit**

```bash
git add 启动-服务.bat 启动-接口.bat
git commit -m "build: Windows 便捷启动批处理(run/serve)"
```

---

## Task 6: 打包说明文档 `docs/打包说明.md`

**Files:**
- Create: `docs/打包说明.md`

- [ ] **Step 1: Create the doc**

````markdown
# 打包说明（Windows EXE）

把 `amap-service` 打成 Windows onedir 可执行文件。一个 exe，子命令区分两个常驻进程：
`amap-service.exe run`（cron 守护）与 `amap-service.exe serve`（HTTP API）。配置外置，改配置不用重打包。

## 前提

- 在 **Windows 机器** 上构建（PyInstaller 不跨平台编译）。
- 已装 Python 3.11。

## 构建步骤

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev,mysql]"   :: 不用 MySQL 可去掉 mysql
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pytest tests\test_packaging_deps.py -v   :: 先确认依赖齐全
.venv\Scripts\pyinstaller amap-service.spec --noconfirm
```

产物在 `dist\amap-service\`。

## 部署目录

```
amap-service\
  amap-service.exe
  _internal\            ← 依赖(勿删)
  config\
    config.yaml         ← 由 config.yaml.example 拷贝并填写
    config.yaml.example
  road_network.db       ← SQLite(或 config 指向外部 / 改 MySQL)
  logs\
  启动-服务.bat
  启动-接口.bat
```

把 `config\config.yaml.example` 拷成 `config\config.yaml` 填好；把两个 `.bat` 拷进该目录。

## 运行

```bat
cd dist\amap-service
amap-service.exe initdb                  :: 建表
amap-service.exe run                     :: 守护进程(进程A)
amap-service.exe serve                   :: API(进程B,另开一个窗口)
```

配置路径默认取 exe 同级 `config\config.yaml`；也可 `-c 其它路径` 覆盖。
敏感项可用环境变量覆盖，如 `set AMAP__TRANSIT__PASSWORD=真实密码`。

## 常见 ImportError 排查

PyInstaller 漏收动态导入时，运行期会 `ModuleNotFoundError: No module named 'X'`。处理：

1. 把 `X` 加进 `amap-service.spec` 的 `hiddenimports`（或对应包用 `collect_all('X')`）。
2. 重新 `pyinstaller amap-service.spec --noconfirm`，再跑。

已知易漏：`pydantic_core`(二进制)、`google.protobuf.internal`、`ijson.backends.*`、
`uvicorn.protocols.*`、`apscheduler` 的 triggers/executors/jobstores。spec 已预置，
若仍报错按上面追加。
````

- [ ] **Step 2: Commit**

```bash
git add docs/打包说明.md
git commit -m "docs: Windows 打包说明(构建/部署/ImportError 排查)"
```

---

## Task 7: Windows 构建与验收（在 Windows 机器上执行）

> 本任务**不在 Mac 上执行**，由操作者在目标 Windows 机器上按清单实跑冻结产物。每条都要看到预期输出才算过；遇 `ModuleNotFoundError` 回 Task 4 的 spec 补 `hiddenimports` 后重打。

- [ ] **Step 1: 装依赖并跑依赖守护测试**

Run: `.venv\Scripts\pip install -e ".[dev,mysql]" pyinstaller` 然后 `.venv\Scripts\pytest tests\test_packaging_deps.py -v`
Expected: 全 PASS（缺失依赖在此暴露，而非等到冻结后）

- [ ] **Step 2: 构建**

Run: `.venv\Scripts\pyinstaller amap-service.spec --noconfirm`
Expected: `dist\amap-service\amap-service.exe` 生成，无 ERROR

- [ ] **Step 3: 准备配置并建表**

Run:
```bat
cd dist\amap-service
copy config\config.yaml.example config\config.yaml
amap-service.exe initdb
```
Expected: 日志 `initdb: tables ensured`，目录下出现 `road_network.db`

- [ ] **Step 4: 一次性拉数据（指向 mock 或真实上游）**

Run: `amap-service.exe run-once road-network`
Expected: 正常返回插入/更新计数，无 ImportError

- [ ] **Step 5: 启动 API 进程并验证 Swagger**

Run: `amap-service.exe serve`（需 config 中 `api.enabled: true`）
Expected: uvicorn 启动日志；浏览器开 `http://<host>:<port>/docs` 出 Swagger UI

- [ ] **Step 6: 启动守护进程并确认与 API 同时跑**

Run: 另开窗口 `amap-service.exe run`
Expected: 日志 `scheduler starting with jobs: [...]`；与 Step 5 的 serve 同时运行，两进程读同一 `road_network.db` 无 “database is locked” 报错（WAL + busy_timeout 已就绪）

- [ ] **Step 7: 记录验收结果**

把跑通情况（含为补漏而追加的 hidden imports）回填到 `docs/打包说明.md` 的排查清单，并提交。

---

## Self-Review

**Spec coverage（对照规格逐节）：**
- §2 一个 exe + run/serve 两进程 → Task 4 spec 单入口 `__main__.py`；Task 5 两个 bat 分别启动 → ✅
- §3 依赖坑（uvicorn/pydantic/protobuf/ijson/apscheduler/sqlalchemy/paho/openpyxl/redis）→ Task 4 spec 全部覆盖 + Task 5(实为 Task 4) 守护测试 `test_packaging_deps.py` → ✅
- §3 datas（config.yaml.example、line_traffic.proto）→ Task 4 spec `datas` → ✅
- §4 冻结后 config 路径锚定 → Task 1+2；SQLite 相对路径锚定 → Task 1+3 → ✅
- §5 构建步骤 + 实跑验收 → Task 6 文档 + Task 7 清单 → ✅
- §6 交付物（spec、cli/engine 改动+测试、bat、打包说明）→ Task 1–6 全覆盖 → ✅
- §7 不在范围（Windows 服务/onefile/安装包/CI）→ 计划未涉及，符合 → ✅

**Placeholder scan：** 无 TBD/TODO；每个代码步骤含完整代码与确切命令、预期输出。Task 7 明确标注为 Windows 机器手工执行（非占位，而是平台约束）。

**Type consistency：** `paths.app_base_dir` / `paths.default_config_path` / `paths.resolve_data_path` 三个函数名在 Task 1 定义，Task 2（`default_config_path`）、Task 3（`resolve_data_path`）引用一致；spec 入口 `amap_service/__main__.py` 与现有文件一致；hiddenimports 中的模块名与 `test_packaging_deps.py` 的 `REQUIRED` 列表一致。
