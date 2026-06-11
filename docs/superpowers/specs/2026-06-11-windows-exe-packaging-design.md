# Windows EXE 打包设计

> 日期：2026-06-11
> 目标：把 `amap-service` 打包成 Windows 可执行文件 + 外置配置文件，便于在 Windows 服务器上部署。

## 1. 背景与目标

`amap_service` 目前是一个 Python 包，单一入口 `amap_service.cli:main`，通过子命令暴露多种角色：

- `run` —— cron 守护进程：定时拉路网/路况/公交落库 + 可选 MQTT 发布。
- `serve` —— HTTP API 进程（FastAPI / uvicorn）。
- `initdb` / `run-once <job>` / `match-report` —— 一次性运维命令。

需求：在 **Windows** 上以「exe + 配置文件」形式部署。改配置不需要重新打包。

### 已确认的决策

| 决策点 | 选择 |
|---|---|
| 目标平台 | Windows 运行，**在 Windows 机器上构建**（PyInstaller 不跨平台编译） |
| 打包工具 | **PyInstaller** |
| exe 数量 | **一个 exe + 子命令**（运行时仍是 run / serve 两个进程） |
| 打包模式 | **onedir**（一个文件夹，启动快、杀软误报少、易排查） |
| 保活 / 自启 | **本次不做**。只产出可跑的 exe；注册为 Windows 服务（WinSW/NSSM/任务计划）由后续决定 |
| 配置 | **完全外置**，放 exe 旁边的 `config\config.yaml` |

## 2. 架构：一个 exe，运行时两个进程

同一份 `amap-service.exe` 用子命令区分角色：

| 启动命令 | 角色 | 进程 |
|---|---|---|
| `amap-service.exe run` | cron 守护（落库 + MQTT） | 进程 A（常驻） |
| `amap-service.exe serve` | HTTP API | 进程 B（常驻） |
| `amap-service.exe initdb` / `run-once <job>` / `match-report` | 运维命令 | 临时进程 |

两个常驻进程**不共享内存**，API 只读 daemon 写入的同一个数据库。运行时是两个独立进程，但打包只产出一个 exe。

### 交付目录结构（onedir 产物）

```
amap-service\
  amap-service.exe          ← 入口（= amap_service.cli:main）
  _internal\                ← PyInstaller 自动放所有依赖（uvicorn/fastapi/protobuf…）
  config\
    config.yaml             ← 外置配置（用户编辑；不进 _internal）
    config.yaml.example     ← 参考模板（随包带，不覆盖用户的 config.yaml）
  road_network.db           ← SQLite 数据库文件（或在 config 中指向外部 / 改用 MySQL）
  logs\                     ← match-report 等输出
  启动-服务.bat             ← 可选：封装 amap-service.exe run
  启动-接口.bat             ← 可选：封装 amap-service.exe serve
```

## 3. 打包脚本与依赖处理（核心工作量）

用一个 `amap-service.spec` 文件统一治理（比命令行参数可维护）。以下依赖含 PyInstaller **不会自动发现** 的动态导入，不显式声明会在运行期 `ModuleNotFoundError`。

### 需要 collect_all / 显式 hidden imports

| 依赖 | 坑 | 处理 |
|---|---|---|
| `uvicorn[standard]` | loops/protocols/websockets/httptools 运行期动态 import | `collect_submodules('uvicorn')` + hiddenimports：`websockets`、`httptools`、`uvicorn.protocols.http.httptools_impl` 等 |
| `pydantic` v2 | Rust 扩展 `pydantic_core` 二进制 | `collect_all('pydantic')` + `collect_all('pydantic_core')` |
| `protobuf` 5.x | `google.protobuf.internal` 运行期校验；本项目 `line_traffic_pb2` | `collect_all('google.protobuf')`；`_pb2` 在 `amap_service/publish/proto/` 包内会自动收 |
| `ijson` | C 后端(yajl) + python 后端按名动态选 | `collect_all('ijson')`（含 `ijson.backends.*`） |
| `APScheduler` | executors/triggers/jobstores 走名字加载 | hiddenimports：`apscheduler.triggers.cron`、`apscheduler.executors.pool`、`apscheduler.jobstores.memory` |
| `SQLAlchemy` | dialect 按字符串名延迟加载 | hiddenimports：`sqlalchemy.dialects.sqlite`；用 MySQL 再加 `pymysql` |
| `paho.mqtt` | 一般 OK | 兜底 hiddenimports：`paho.mqtt.client` |
| `openpyxl` | match-report 才用 | `collect_submodules('openpyxl')` |
| `redis` | 一般 OK | 兜底 hiddenimports：`redis` |

### datas（非代码文件）

- `config/config.yaml.example` → 随包，供用户参考。
- `proto/line_traffic.proto` → 契约文档（`_pb2.py` 是代码会自动收）。

### spec 关键参数

- `name = 'amap-service'`
- onedir（不加 `--onefile`）
- `console = True`（服务程序需要 stdout 日志）

> **风险**：hidden imports 列表靠经验 + 实测收敛，不可能一次写全。`pydantic_core` / `protobuf` 两个二进制扩展最易漏。验收必须在 Windows 上实跑冻结产物（见 §5），遇 ImportError 回 spec 补齐再重打。

## 4. 配置 / DB 路径解析（冻结后的小改动）

`load_config` 默认 `config/config.yaml` 现为**相对 cwd**。双击 exe 或从别处启动时 cwd 不可靠，会找不到配置。需让冻结后的 exe 稳定锚定到自身目录：

- 在 `cli.py` 增加 `_default_config_path()`：
  - `getattr(sys, "frozen", False)` 为真（PyInstaller）→ 基准目录 = `Path(sys.executable).parent`。
  - 否则（源码运行）→ 维持相对 `config/config.yaml`，对开发与测试零影响。
  - 默认值 = `基准目录 / "config" / "config.yaml"`。
- `-c/--config` 显式传参一律以用户为准（绝对/相对皆可），覆盖默认。
- **SQLite DB 相对路径同理锚定到 exe 目录**（在 `db/engine.py` 处理），与 config 行为一致，避免 cwd 坑。示例配置中 DB 用相对 exe 的路径并在文档写明。

改动范围：`cli.py` 一处 + `db/engine.py` 一处 + 对应测试。源码运行行为不变。

## 5. 构建步骤与验收

### 构建（Windows 机器，一次性）

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[mysql]"   :: 要 MySQL 就带上；否则去掉
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller amap-service.spec --noconfirm
```

产物在 `dist\amap-service\`。把由 example 拷贝并填好的 `config\config.yaml` 放进去。

### 验收（必须对冻结产物实跑，不能只信构建成功）

```bat
cd dist\amap-service
amap-service.exe initdb
amap-service.exe run-once road-network -c config\config.yaml   :: 指向 mock 或真实上游
amap-service.exe serve                     :: 浏览器开 /docs 出 Swagger
amap-service.exe run                       :: 守护起来，cron 注册成功，日志正常
```

逐条跑通，遇 `ModuleNotFoundError` 回 spec 补 hidden import 重打。`serve` 与 `run` 同时各起一个进程，确认两进程读同一 DB 协作正常。

## 6. 交付物清单

1. `amap-service.spec` —— PyInstaller 打包脚本（含全部 hidden imports / datas）。
2. `cli.py` / `db/engine.py` 的冻结路径锚定改动 + 对应测试。
3. `启动-服务.bat`、`启动-接口.bat`（可选便捷脚本）。
4. `docs/打包说明.md` —— Windows 构建步骤、目录布局、改配置免重打、常见 ImportError 排查清单。

## 7. 不在范围内（YAGNI）

- 注册 Windows 服务 / 开机自启 / 崩溃保活（后续单独决定）。
- onefile 模式、跨平台（Linux/Mac）打包。
- 安装包（MSI/Inno Setup）—— 当前交付是一个可拷贝的文件夹。
- CI 自动构建 Windows 产物。
