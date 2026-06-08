# 高德地图数据服务 — Plan 1：Foundation（脚手架 + 配置 + 数据层）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Commit 约定：** 每个 commit message 末尾追加一行 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`（下文步骤为简洁省略，执行时补上）。

**Goal:** 搭起 `amap_service` 包骨架，实现 YAML+pydantic 配置加载与方言无关的数据层（engine / schema / migrate / repositories upsert），为后续 SDK、流水线、调度提供地基。

**Architecture:** 模块化单体包。配置层用 pydantic v2 强类型校验（含 cron 合法性、按方言条件校验、`AMAP__SECTION__KEY` 环境变量覆盖）。数据层用 SQLAlchemy 2.0 Core 定义表与索引，`init_db` 幂等建表，repositories 提供按 `link_id` 的方言无关 upsert（路网坐标整段替换、路况只存最新刷新时间）。

**Tech Stack:** Python 3.11、pydantic 2.x、PyYAML、SQLAlchemy 2.0 Core、APScheduler 3.x（仅用其 cron 校验）、pytest。数据库默认 SQLite（MySQL 经 PyMySQL，可选）。

**对应 spec：** [docs/superpowers/specs/2026-05-29-amap-service-design.md](../specs/2026-05-29-amap-service-design.md) 第 3、4、5 节与里程碑 M1。

**关于 traffic_status.link_id 的有意偏离：** 数据字典把 `link_id` 标为普通 INDEX（因其设想多快照）。本服务已确认「upsert 只存最新」，故 `link_id` 必须为 **UNIQUE**（ON CONFLICT 的前提）。这是与字典文字的有意一致性偏离，已在 spec 决策表记录。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `pyproject.toml` | 包元数据、依赖、pytest 配置（`pythonpath=["."]`） |
| `amap_service/__init__.py` | 包标识 |
| `amap_service/config/__init__.py` | 子包标识 |
| `amap_service/config/schema.py` | pydantic 配置模型 + cron 校验 |
| `amap_service/config/loader.py` | 读 YAML + 环境变量覆盖 + 校验 |
| `amap_service/db/__init__.py` | 子包标识 |
| `amap_service/db/schema.py` | SQLAlchemy Core 表定义（5 张表 + 索引） |
| `amap_service/db/engine.py` | 按配置构造 Engine（sqlite/mysql） |
| `amap_service/db/migrate.py` | 幂等建表 |
| `amap_service/db/repositories.py` | 方言无关 upsert：路网 + 坐标替换、路况 |
| `tests/...` | 与各模块对应的测试 |

---

## Task 1：项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `amap_service/__init__.py`
- Create: `amap_service/config/__init__.py`
- Create: `amap_service/db/__init__.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: 写冒烟测试（先失败）**

`tests/test_smoke.py`:
```python
def test_package_imports():
    import amap_service
    assert amap_service is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_smoke.py -q`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'amap_service'`

- [ ] **Step 3: 创建包与 pyproject**

`pyproject.toml`:
```toml
[project]
name = "amap-service"
version = "0.1.0"
description = "Amap map-data landing service (road network, traffic, transit, GPS↔link SDK)"
requires-python = ">=3.11"
dependencies = [
  "pydantic>=2",
  "PyYAML>=6",
  "SQLAlchemy>=2",
  "APScheduler>=3.10,<4",
  "httpx>=0.27",
  "ijson>=3.2",
  "redis>=5",
]

[project.optional-dependencies]
mysql = ["PyMySQL>=1.1"]
dev = ["pytest>=8", "fakeredis>=2"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["amap_service*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
# importlib mode avoids basename collisions (e.g. tests/config/test_schema.py vs tests/db/test_schema.py)
addopts = "--import-mode=importlib"
```

`amap_service/__init__.py`:
```python
"""Amap map-data landing service."""

__version__ = "0.1.0"
```

`amap_service/config/__init__.py`:
```python
```

`amap_service/db/__init__.py`:
```python
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_smoke.py -q`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml amap_service/ tests/test_smoke.py
git commit -m "chore: scaffold amap_service package and pytest config"
```

---

## Task 2：配置模型（pydantic schema）

**Files:**
- Create: `amap_service/config/schema.py`
- Test: `tests/config/test_schema.py`

- [ ] **Step 1: 写失败测试**

`tests/config/test_schema.py`:
```python
import pytest
from pydantic import ValidationError
from amap_service.config.schema import AppConfig

def _minimal():
    return {
        "amap": {
            "endpoint": "http://192.168.102.102:8080",
            "jobs": {
                "road_network": {"path": "/g5_server/map/api/areaLinkPub", "cron": "0 1 * * *"},
                "traffic_status": {"path": "/g5_server/map/api/traffic/status", "cron": "*/2 * * * *"},
            },
        },
        "transit": {
            "username": "yangs", "password": "pw",
            "token_url": "http://t", "line_list_url": "http://l", "line_entity_url": "http://e",
        },
    }

def test_defaults_applied():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.database.type == "sqlite"
    assert cfg.database.sqlite.path == "./road_network.db"
    assert cfg.redis.enabled is False
    assert cfg.redis.uses.token_cache is True
    assert cfg.http.max_retries == 3
    assert cfg.sdk.match_tolerance_m == 30
    assert cfg.amap.auth.type == "none"

def test_invalid_cron_rejected():
    data = _minimal()
    data["amap"]["jobs"]["road_network"]["cron"] = "not a cron"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)

def test_mysql_requires_block():
    data = _minimal()
    data["database"] = {"type": "mysql"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)

def test_invalid_db_type_rejected():
    data = _minimal()
    data["database"] = {"type": "postgres"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/config/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError`/import error for `amap_service.config.schema`

- [ ] **Step 3: 实现 schema**

`amap_service/config/schema.py`:
```python
from typing import Literal, Optional

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_cron(value: str) -> str:
    # Raises ValueError on malformed expression → pydantic surfaces as ValidationError.
    CronTrigger.from_crontab(value)
    return value


class AuthConfig(BaseModel):
    type: Literal["none", "header"] = "none"
    headers: dict[str, str] = Field(default_factory=dict)


class JobConfig(BaseModel):
    path: str
    cron: str
    enabled: bool = True

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)


class AmapJobs(BaseModel):
    road_network: JobConfig
    traffic_status: JobConfig


class AmapConfig(BaseModel):
    endpoint: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jobs: AmapJobs


class TransitConfig(BaseModel):
    enabled: bool = True
    cron: str = "0 3 * * *"
    username: str
    password: str
    token_url: str
    line_list_url: str
    line_entity_url: str

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)


class SqliteConfig(BaseModel):
    path: str = "./road_network.db"


class MysqlConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "amap"
    password: str = ""
    database: str = "road_network"
    charset: str = "utf8mb4"


class DatabaseConfig(BaseModel):
    type: Literal["sqlite", "mysql"] = "sqlite"
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    mysql: Optional[MysqlConfig] = None

    @model_validator(mode="after")
    def _require_mysql_block(self) -> "DatabaseConfig":
        if self.type == "mysql" and self.mysql is None:
            raise ValueError("database.type=mysql requires a 'mysql' block")
        return self


class RedisUses(BaseModel):
    latest_traffic_snapshot: bool = True
    incremental_detection: bool = True
    token_cache: bool = True


class RedisConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    uses: RedisUses = Field(default_factory=RedisUses)


class HttpConfig(BaseModel):
    timeout_seconds: int = 30
    max_retries: int = 3
    backoff_seconds: float = 2.0


class SdkConfig(BaseModel):
    match_tolerance_m: float = 30.0
    reverse_angle_deg: float = 90.0
    dedup_jitter_m: float = 5.0


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None


class AppConfig(BaseModel):
    amap: AmapConfig
    transit: TransitConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    sdk: SdkConfig = Field(default_factory=SdkConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/config/test_schema.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add amap_service/config/schema.py tests/config/test_schema.py
git commit -m "feat(config): pydantic schema with cron + dialect validation"
```

---

## Task 3：配置加载器（YAML + 环境变量覆盖）

**Files:**
- Create: `amap_service/config/loader.py`
- Test: `tests/config/test_loader.py`

- [ ] **Step 1: 写失败测试**

`tests/config/test_loader.py`:
```python
import textwrap
from amap_service.config.loader import load_config

YAML = textwrap.dedent(
    """
    amap:
      endpoint: "http://192.168.102.102:8080"
      jobs:
        road_network: {path: "/g5_server/map/api/areaLinkPub", cron: "0 1 * * *"}
        traffic_status: {path: "/g5_server/map/api/traffic/status", cron: "*/2 * * * *"}
    transit:
      username: "yangs"
      password: "pw"
      token_url: "http://t"
      line_list_url: "http://l"
      line_entity_url: "http://e"
    redis:
      enabled: true
      port: 6379
    """
)

def _write(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p

def test_loads_yaml(tmp_path):
    cfg = load_config(_write(tmp_path), environ={})
    assert cfg.amap.endpoint == "http://192.168.102.102:8080"
    assert cfg.amap.jobs.traffic_status.cron == "*/2 * * * *"
    assert cfg.database.type == "sqlite"

def test_env_override_scalar_and_coercion(tmp_path):
    env = {"AMAP__REDIS__PORT": "6380", "AMAP__TRANSIT__PASSWORD": "secret"}
    cfg = load_config(_write(tmp_path), environ=env)
    assert cfg.redis.port == 6380          # coerced str -> int by pydantic
    assert cfg.transit.password == "secret"

def test_env_override_ignores_unrelated_keys(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": "/root"}
    cfg = load_config(_write(tmp_path), environ=env)
    assert cfg.transit.password == "pw"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/config/test_loader.py -q`
Expected: FAIL — import error for `amap_service.config.loader`

- [ ] **Step 3: 实现 loader**

`amap_service/config/loader.py`:
```python
import os
from pathlib import Path
from typing import Mapping, Optional

import yaml

from .schema import AppConfig

ENV_PREFIX = "AMAP__"


def _apply_env_overrides(data: dict, environ: Mapping[str, str]) -> dict:
    """Override config values from AMAP__SECTION__KEY env vars (double-underscore = nesting)."""
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX):].lower().split("__")
        if not path or path[-1] == "":
            continue
        node = data
        ok = True
        for part in path[:-1]:
            child = node.get(part)
            if child is None:
                child = {}
                node[part] = child
            if not isinstance(child, dict):
                ok = False
                break
            node = child
        if ok:
            node[path[-1]] = value
    return data


def load_config(path, environ: Optional[Mapping[str, str]] = None) -> AppConfig:
    environ = os.environ if environ is None else environ
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    raw = _apply_env_overrides(raw, environ)
    return AppConfig.model_validate(raw)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/config/test_loader.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add amap_service/config/loader.py tests/config/test_loader.py
git commit -m "feat(config): YAML loader with AMAP__ env overrides"
```

---

## Task 4：数据库 Engine 工厂

**Files:**
- Create: `amap_service/db/engine.py`
- Test: `tests/db/test_engine.py`

- [ ] **Step 1: 写失败测试**

`tests/db/test_engine.py`:
```python
from sqlalchemy import text
from amap_service.config.schema import DatabaseConfig, SqliteConfig, MysqlConfig
from amap_service.db.engine import build_url, make_engine

def test_sqlite_url():
    cfg = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path="./road_network.db"))
    assert build_url(cfg) == "sqlite:///./road_network.db"

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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_engine.py -q`
Expected: FAIL — import error for `amap_service.db.engine`

- [ ] **Step 3: 实现 engine**

`amap_service/db/engine.py`:
```python
from sqlalchemy import Engine, create_engine

from amap_service.config.schema import DatabaseConfig


def build_url(db: DatabaseConfig) -> str:
    if db.type == "sqlite":
        return f"sqlite:///{db.sqlite.path}"
    if db.type == "mysql":
        m = db.mysql
        return (
            f"mysql+pymysql://{m.user}:{m.password}@{m.host}:{m.port}/{m.database}"
            f"?charset={m.charset}"
        )
    raise ValueError(f"unsupported database type: {db.type}")


def make_engine(db: DatabaseConfig) -> Engine:
    return create_engine(build_url(db), future=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/db/test_engine.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add amap_service/db/engine.py tests/db/test_engine.py
git commit -m "feat(db): engine factory for sqlite/mysql"
```

---

## Task 5：表结构定义（SQLAlchemy Core）

**Files:**
- Create: `amap_service/db/schema.py`
- Test: `tests/db/test_schema.py`

- [ ] **Step 1: 写失败测试**

`tests/db/test_schema.py`:
```python
from amap_service.db.schema import (
    metadata, road_link, road_link_coord, traffic_status, transit_line_raw,
)

def test_tables_registered():
    names = set(metadata.tables.keys())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= names

def test_road_link_columns():
    cols = set(road_link.c.keys())
    assert {"id", "link_id", "road_name", "length", "formway",
            "roadclass", "line_track", "created_at"} == cols
    assert road_link.c.link_id.unique is True

def test_coord_columns_and_unique():
    cols = set(road_link_coord.c.keys())
    assert {"id", "link_id", "seq", "longitude", "latitude"} == cols

def test_traffic_link_id_unique_for_upsert():
    # upsert-latest semantics require a unique link_id (deviation from data-dict INDEX)
    assert traffic_status.c.link_id.unique is True
    assert set(traffic_status.c.keys()) == {
        "id", "link_id", "speed", "state", "travel_time", "updated_at"
    }

def test_transit_raw_columns():
    assert set(transit_line_raw.c.keys()) == {"id", "line_name", "raw_response", "fetched_at"}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_schema.py -q`
Expected: FAIL — import error for `amap_service.db.schema`

- [ ] **Step 3: 实现 schema**

`amap_service/db/schema.py`:
```python
from sqlalchemy import (
    BigInteger, Column, Float, Index, Integer, MetaData, Table, Text,
    TIMESTAMP, UniqueConstraint, func,
)

metadata = MetaData()

road_link = Table(
    "road_link", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False, unique=True),
    Column("road_name", Text),
    Column("length", Integer),
    Column("formway", Integer),
    Column("roadclass", Integer),
    Column("line_track", Text),
    Column("created_at", TIMESTAMP, server_default=func.current_timestamp()),
    Index("idx_road_link_road_name", "road_name"),
    Index("idx_road_link_formway", "formway"),
    Index("idx_road_link_roadclass", "roadclass"),
)

road_link_coord = Table(
    "road_link_coord", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("latitude", Float, nullable=False),
    UniqueConstraint("link_id", "seq", name="idx_road_link_coord_uniq"),
    Index("idx_road_link_coord_lid", "link_id"),
)

traffic_status = Table(
    "traffic_status", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False, unique=True),
    Column("speed", Integer),
    Column("state", Integer),
    Column("travel_time", Integer),
    Column("updated_at", TIMESTAMP, server_default=func.current_timestamp()),
    Index("idx_traffic_status_state", "state"),
    Index("idx_traffic_status_updated", "updated_at"),
)

transit_line_raw = Table(
    "transit_line_raw", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("line_name", Text),
    Column("raw_response", Text),
    Column("fetched_at", TIMESTAMP, server_default=func.current_timestamp()),
)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/db/test_schema.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add amap_service/db/schema.py tests/db/test_schema.py
git commit -m "feat(db): core table definitions and indexes"
```

---

## Task 6：幂等建表（migrate）

**Files:**
- Create: `amap_service/db/migrate.py`
- Test: `tests/db/test_migrate.py`

- [ ] **Step 1: 写失败测试**

`tests/db/test_migrate.py`:
```python
from sqlalchemy import inspect
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db

def test_init_db_idempotent(tmp_path):
    cfg = DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db")))
    engine = make_engine(cfg)
    init_db(engine)
    init_db(engine)  # second call must not raise
    tables = set(inspect(engine).get_table_names())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= tables
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_migrate.py -q`
Expected: FAIL — import error for `amap_service.db.migrate`

- [ ] **Step 3: 实现 migrate**

`amap_service/db/migrate.py`:
```python
from sqlalchemy import Engine

from .schema import metadata


def init_db(engine: Engine) -> None:
    """Create all tables and indexes if absent. Idempotent."""
    metadata.create_all(engine, checkfirst=True)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/db/test_migrate.py -q`
Expected: PASS（1 passed）

- [ ] **Step 5: 提交**

```bash
git add amap_service/db/migrate.py tests/db/test_migrate.py
git commit -m "feat(db): idempotent init_db"
```

---

## Task 7：Repositories（方言无关 upsert）

**Files:**
- Create: `amap_service/db/repositories.py`
- Test: `tests/db/test_repositories.py`

输入数据形状（plain dict，避免依赖后续 parsing 类型）：
- 路网 link：`{"link_id": int, "road_name": str|None, "length": int|None, "formway": int|None, "roadclass": int|None, "line_track": str|None, "coords": [(lng, lat), ...]}`
- 路况 row：`{"link_id": int, "speed": int|None, "state": int|None, "travel_time": int|None}`

- [ ] **Step 1: 写失败测试**

`tests/db/test_repositories.py`:
```python
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import road_link, road_link_coord, traffic_status
from amap_service.db.repositories import upsert_road_links, upsert_traffic_status


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def test_road_link_insert_then_update_replaces_coords(tmp_path):
    e = _engine(tmp_path)
    link = {
        "link_id": 5130091959790075998, "road_name": "G50沪渝高速",
        "length": 328, "formway": 1, "roadclass": 0,
        "line_track": "120.9374,31.0603;120.9343,31.0591;120.93,31.05",
        "coords": [(120.9374, 31.0603), (120.9343, 31.0591), (120.93, 31.05)],
    }
    stats = upsert_road_links(e, [link])
    assert stats["inserted"] == 1 and stats["updated"] == 0 and stats["failed"] == 0

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 1
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 3
        # 64-bit link_id preserved exactly
        assert c.execute(select(road_link.c.link_id)).scalar() == 5130091959790075998

    updated = dict(link, road_name="改名路", coords=[(1.0, 2.0), (3.0, 4.0)])
    stats2 = upsert_road_links(e, [updated])
    assert stats2["inserted"] == 0 and stats2["updated"] == 1

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 1
        assert c.execute(select(road_link.c.road_name)).scalar() == "改名路"
        # coords fully replaced (3 -> 2)
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 2


def test_traffic_upsert_latest_only(tmp_path):
    e = _engine(tmp_path)
    rid = 5130516143645130888
    s1 = upsert_traffic_status(e, [{"link_id": rid, "speed": 89, "state": 1, "travel_time": 59}])
    assert s1["inserted"] == 1 and s1["updated"] == 0
    s2 = upsert_traffic_status(e, [{"link_id": rid, "speed": 40, "state": 3, "travel_time": 120}])
    assert s2["inserted"] == 0 and s2["updated"] == 1

    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 1
        row = c.execute(
            select(traffic_status.c.speed, traffic_status.c.state, traffic_status.c.travel_time)
        ).one()
        assert tuple(row) == (40, 3, 120)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_repositories.py -q`
Expected: FAIL — import error for `amap_service.db.repositories`

- [ ] **Step 3: 实现 repositories**

`amap_service/db/repositories.py`:
```python
import logging
from typing import Iterable

from sqlalchemy import Engine, Connection, delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .schema import road_link, road_link_coord, traffic_status

logger = logging.getLogger(__name__)


def _upsert_stmt(conn: Connection, table, values: dict, index_col, update_cols: list[str]):
    """Build a dialect-appropriate INSERT ... ON CONFLICT/DUPLICATE UPDATE statement."""
    name = conn.dialect.name
    if name == "sqlite":
        stmt = sqlite_insert(table).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=[index_col],
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
    if name == "mysql":
        stmt = mysql_insert(table).values(**values)
        return stmt.on_duplicate_key_update(
            **{c: getattr(stmt.inserted, c) for c in update_cols}
        )
    raise ValueError(f"unsupported dialect for upsert: {name}")


def _batched(items: Iterable, size: int):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_road_links(engine: Engine, links: Iterable[dict], batch_size: int = 2000) -> dict:
    """Upsert road_link rows by link_id; fully replace each link's coords (delete + reinsert)."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    for batch in _batched(links, batch_size):
        ids = [l["link_id"] for l in batch]
        try:
            with engine.begin() as conn:
                existing = set(
                    conn.execute(
                        select(road_link.c.link_id).where(road_link.c.link_id.in_(ids))
                    ).scalars().all()
                )
                for link in batch:
                    _upsert_one_link(conn, link)
                inserted = sum(1 for lid in ids if lid not in existing)
            stats["inserted"] += inserted
            stats["updated"] += len(batch) - inserted
        except Exception:
            stats["failed"] += len(batch)
            logger.exception("road_link batch failed (%d rows)", len(batch))
    return stats


def _upsert_one_link(conn: Connection, link: dict) -> None:
    stmt = _upsert_stmt(
        conn, road_link,
        values={
            "link_id": link["link_id"],
            "road_name": link.get("road_name"),
            "length": link.get("length"),
            "formway": link.get("formway"),
            "roadclass": link.get("roadclass"),
            "line_track": link.get("line_track"),
        },
        index_col=road_link.c.link_id,
        update_cols=["road_name", "length", "formway", "roadclass", "line_track"],
    )
    conn.execute(stmt)
    conn.execute(delete(road_link_coord).where(road_link_coord.c.link_id == link["link_id"]))
    coords = link.get("coords") or []
    if coords:
        conn.execute(
            road_link_coord.insert(),
            [
                {"link_id": link["link_id"], "seq": i, "longitude": lng, "latitude": lat}
                for i, (lng, lat) in enumerate(coords)
            ],
        )


def upsert_traffic_status(engine: Engine, rows: Iterable[dict], batch_size: int = 2000) -> dict:
    """Upsert traffic_status by link_id (latest-only), refreshing updated_at."""
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    for batch in _batched(rows, batch_size):
        ids = [r["link_id"] for r in batch]
        try:
            with engine.begin() as conn:
                existing = set(
                    conn.execute(
                        select(traffic_status.c.link_id).where(traffic_status.c.link_id.in_(ids))
                    ).scalars().all()
                )
                for row in batch:
                    stmt = _upsert_stmt(
                        conn, traffic_status,
                        values={
                            "link_id": row["link_id"],
                            "speed": row.get("speed"),
                            "state": row.get("state"),
                            "travel_time": row.get("travel_time"),
                            "updated_at": func.current_timestamp(),
                        },
                        index_col=traffic_status.c.link_id,
                        update_cols=["speed", "state", "travel_time", "updated_at"],
                    )
                    conn.execute(stmt)
                inserted = sum(1 for lid in ids if lid not in existing)
            stats["inserted"] += inserted
            stats["updated"] += len(batch) - inserted
        except Exception:
            stats["failed"] += len(batch)
            logger.exception("traffic_status batch failed (%d rows)", len(batch))
    return stats
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/db/test_repositories.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 全量回归 + 提交**

Run: `pytest -q`
Expected: PASS（全部测试通过）

```bash
git add amap_service/db/repositories.py tests/db/test_repositories.py
git commit -m "feat(db): dialect-aware upsert for road links and traffic"
```

---

## 完成标准（Definition of Done）

- `pytest -q` 全绿。
- `python -c "import amap_service"` 成功。
- 能用一份示例 `config/config.yaml` 跑通：`load_config` → `make_engine` → `init_db` → `upsert_road_links` / `upsert_traffic_status`。
- 64 位 `link_id`（如 5130091959790075998）经 upsert 后原样保留（测试已覆盖）。
- 路网坐标整段替换、路况只存最新均经测试验证。

## 后续 Plan（不在本计划内）

- **Plan 2 — SDK（需求3）**：`sdk/geometry.py`（纯函数）→ `sdk/matcher.py`（DB 只读空间匹配）→ `sdk/conversion.py`（双向转换 + reverse_coords）。
- **Plan 3 — 需求1 流水线**：`clients/`（httpx + 重试 + 流式）、`parsing/`（coordList 成对、分段聚合、ijson 流式）、`pipelines/road_network.py`、`pipelines/traffic.py`。
- **Plan 4 — 调度 daemon + CLI + cache**：`scheduler/runner.py`（APScheduler 装配 cron）、`cli.py`（run/run-once/initdb）、`cache/client.py`（NoOp/Redis）。
- **Plan 5 — 需求2 阶段一**：`clients/transit.py`（MD5 签名 + token 缓存）、`pipelines/transit.py`（token→列表→对象 链路 + 原始响应存档到 `logs/transit_raw/` 与 `transit_line_raw`）。
