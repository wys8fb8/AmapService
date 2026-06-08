# 高德地图数据服务 — Plan 4：调度 daemon + CLI + 可选缓存

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **Commit 约定：** commit message 末尾追加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`，用 `git -c user.name='Claude' -c user.email='noreply@anthropic.com' commit`。

**Goal:** 把需求1 两条流水线接上「配置驱动 cron 的长驻 daemon」，提供 CLI（`initdb` / `run-once <job>` / `run`），并实现可选 Redis 缓存（未启用→NoOp）：**最新路况快照** + **增量/变更检测**（跳过未变 link 的 DB 写入）。token 缓存留到 Plan 5（需求2）。

**Architecture:** `cache/client.py` 提供统一接口的 `NoOpCache`（未启用）与 `RedisCache`（启用），`make_cache(redis_config)` 工厂据 `enabled` 选择。路况流水线 `run_traffic` 增加可选 `cache` + `snapshot`/`incremental` 开关：启用时按 link 比对签名跳过未变项、并写最新快照；未启用时保持原 generator 流式路径不变。`scheduler/runner.py` 的 `build_scheduler` 据配置为各 enabled job 用 `CronTrigger.from_crontab` 注册任务（`max_instances=1`+`coalesce=True` 防自我堆叠）。`cli.py` 装配 config→engine→client→cache→scheduler。

**Tech Stack:** APScheduler 3.11（BlockingScheduler + CronTrigger）、redis 7.4（运行时，仅启用时导入）、fakeredis 2.35（测试）、argparse、复用 Plan 1–3 全部模块、pytest。

**对应 spec：** [设计文档](../specs/2026-05-29-amap-service-design.md) 第 3、4、8 节 + 里程碑 M5。**前置：** Plan 1–3（已合并 main）。

**关键约定：**
- 缓存接口最小化：`enabled`、`get(key)->str|None`、`set(key, value, ttl=None)`。RedisCache 把 bytes 解码为 str；NoOpCache 全部空操作。
- 增量检测签名 = `"{speed}:{state}:{travel_time}"`，键 `traffic:sig:{link_id}`；变更才 upsert 并刷新签名。最新快照键 `traffic:latest:{link_id}`，值为该行 JSON。
- 缓存启用时 `run_traffic` 需要对行做多次遍历，故会 `list(rows)`（牺牲流式）；**未启用缓存时保持 generator 流式**，行为与 Plan 3 完全一致（既有测试不变）。
- 调度任务用「显式绑定」避免 Python 闭包延迟绑定坑（两个 job 用不同变量名 `rn`/`ts`）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `amap_service/cache/__init__.py` | 子包标识 |
| `amap_service/cache/client.py` | `NoOpCache` / `RedisCache` / `make_cache` |
| `amap_service/pipelines/traffic.py` | （修改）`run_traffic` 增加可选缓存：增量检测 + 最新快照 |
| `amap_service/scheduler/__init__.py` | 子包标识 |
| `amap_service/scheduler/runner.py` | `build_scheduler(config, engine, http_client, cache)` |
| `amap_service/cli.py` | `initdb` / `run-once <job>` / `run` + `main(argv)` |
| `pyproject.toml` | （修改）增加 `[project.scripts] amap-service` |
| `tests/cache|scheduler/...`、`tests/test_cli.py` | 对应测试 |

---

## Task 1：缓存抽象（NoOpCache / RedisCache / make_cache）

**Files:**
- Create: `amap_service/cache/__init__.py`（空）
- Create: `amap_service/cache/client.py`
- Test: `tests/cache/__init__.py`（空）、`tests/cache/test_client.py`

- [ ] **Step 1: 写失败测试**

`tests/cache/test_client.py`:
```python
import fakeredis
from amap_service.config.schema import RedisConfig
from amap_service.cache.client import NoOpCache, RedisCache, make_cache


def test_noop_cache_is_inert():
    c = NoOpCache()
    assert c.enabled is False
    assert c.get("k") is None
    c.set("k", "v")          # must not raise
    assert c.get("k") is None


def test_redis_cache_set_get_roundtrip_decodes_str():
    c = RedisCache(fakeredis.FakeRedis())
    assert c.enabled is True
    assert c.get("missing") is None
    c.set("k", "v")
    assert c.get("k") == "v"      # bytes decoded to str


def test_redis_cache_ttl():
    r = fakeredis.FakeRedis()
    c = RedisCache(r)
    c.set("k", "v", ttl=100)
    assert c.get("k") == "v"
    assert r.ttl("k") > 0


def test_make_cache_disabled_returns_noop():
    cache = make_cache(RedisConfig(enabled=False))
    assert isinstance(cache, NoOpCache)


def test_make_cache_enabled_returns_redis(monkeypatch):
    # avoid a real server: stub redis.Redis with fakeredis
    import amap_service.cache.client as mod
    monkeypatch.setattr(mod, "_redis_client_from_config", lambda cfg: fakeredis.FakeRedis())
    cache = make_cache(RedisConfig(enabled=True, host="x", port=1))
    assert isinstance(cache, RedisCache)
    cache.set("k", "v")
    assert cache.get("k") == "v"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/cache/test_client.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/cache/__init__.py`: 空文件
`tests/cache/__init__.py`: 空文件

`amap_service/cache/client.py`:
```python
"""Optional Redis cache. NoOpCache when disabled — callers need no `if enabled` branches."""
from typing import Optional

from amap_service.config.schema import RedisConfig


class NoOpCache:
    enabled = False

    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        pass


class RedisCache:
    enabled = True

    def __init__(self, client):
        self._r = client

    def get(self, key: str) -> Optional[str]:
        value = self._r.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        if ttl:
            self._r.setex(key, ttl, value)
        else:
            self._r.set(key, value)


def _redis_client_from_config(cfg: RedisConfig):
    import redis  # imported lazily so a disabled cache needs no redis server/lib at runtime

    return redis.Redis(host=cfg.host, port=cfg.port, db=cfg.db, password=cfg.password)


def make_cache(cfg: RedisConfig):
    """Return RedisCache when enabled, else NoOpCache."""
    if not cfg.enabled:
        return NoOpCache()
    return RedisCache(_redis_client_from_config(cfg))
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/cache/test_client.py -q`
Expected: PASS（5 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/cache/__init__.py amap_service/cache/client.py tests/cache/__init__.py tests/cache/test_client.py
git commit -m "feat(cache): NoOp/Redis cache with make_cache factory"
```

---

## Task 2：路况流水线接入缓存（增量检测 + 最新快照）

**Files:**
- Modify: `amap_service/pipelines/traffic.py`
- Test: `tests/pipelines/test_traffic_cache.py`

- [ ] **Step 1: 写失败测试**

`tests/pipelines/test_traffic_cache.py`:
```python
import json
import httpx
import fakeredis
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.cache.client import RedisCache
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import traffic_status
from amap_service.pipelines.traffic import run_traffic

PAYLOAD = {"linkStates": [
    {"linkId": 1, "speed": 80, "state": 1, "travelTime": 10},
    {"linkId": 2, "speed": 50, "state": 2, "travelTime": 20},
]}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client():
    return HttpClient(backoff_seconds=0,
                      transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD)))


def test_incremental_skips_unchanged_on_second_run(tmp_path):
    e = _engine(tmp_path)
    cache = RedisCache(fakeredis.FakeRedis())
    s1 = run_traffic(e, _client(), "http://h", "/p", cache=cache, incremental=True)
    assert s1["inserted"] == 2
    # identical payload → all unchanged → nothing upserted
    s2 = run_traffic(e, _client(), "http://h", "/p", cache=cache, incremental=True)
    assert s2["inserted"] == 0 and s2["updated"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 2


def test_snapshot_written_to_cache(tmp_path):
    e = _engine(tmp_path)
    r = fakeredis.FakeRedis()
    cache = RedisCache(r)
    run_traffic(e, _client(), "http://h", "/p", cache=cache, snapshot=True)
    snap = json.loads(r.get("traffic:latest:1").decode())
    assert snap["speed"] == 80 and snap["state"] == 1 and snap["travel_time"] == 10


def test_no_cache_path_unchanged(tmp_path):
    # cache=None keeps the plain streaming/generator behavior
    e = _engine(tmp_path)
    s = run_traffic(e, _client(), "http://h", "/p")
    assert s["inserted"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/pipelines/test_traffic_cache.py -q`
Expected: FAIL — `run_traffic` 不接受 cache/incremental/snapshot 参数

- [ ] **Step 3: 修改 run_traffic**

把 `amap_service/pipelines/traffic.py` 整体替换为：
```python
"""Realtime traffic landing pipeline: fetch traffic/status → parse → upsert (latest-only).

Optional Redis cache:
  incremental — skip links whose (speed,state,travel_time) signature is unchanged.
  snapshot    — write each landed row's latest values to the cache.
When no enabled cache is supplied, the original streaming/generator path is used unchanged.
"""
import json
import logging

from sqlalchemy import Engine

from amap_service.clients.base import HttpClient
from amap_service.db.repositories import upsert_traffic_status
from amap_service.parsing.traffic import parse_traffic, parse_traffic_item

logger = logging.getLogger(__name__)


def _signature(row: dict) -> str:
    return f"{row['speed']}:{row['state']}:{row['travel_time']}"


def run_traffic(
    engine: Engine,
    http_client: HttpClient,
    endpoint: str,
    path: str,
    parse_mode: str = "memory",
    cache=None,
    snapshot: bool = False,
    incremental: bool = False,
) -> dict:
    url = endpoint.rstrip("/") + path
    logger.info("traffic: fetching %s (mode=%s)", url, parse_mode)
    if parse_mode == "memory":
        rows = parse_traffic(http_client.get_json(url))
    elif parse_mode == "stream":
        rows = (parse_traffic_item(it) for it in http_client.stream_items(url, "linkStates.item"))
    else:
        raise ValueError(f"unknown parse_mode: {parse_mode}")

    use_cache = cache is not None and getattr(cache, "enabled", False) and (snapshot or incremental)
    if not use_cache:
        stats = upsert_traffic_status(engine, rows)
        logger.info("traffic: done %s", stats)
        return stats

    rows = list(rows)  # cache path needs multiple passes over the rows
    if incremental:
        changed = []
        for row in rows:
            key = f"traffic:sig:{row['link_id']}"
            sig = _signature(row)
            if cache.get(key) != sig:
                cache.set(key, sig)
                changed.append(row)
        rows = changed

    stats = upsert_traffic_status(engine, rows)

    if snapshot:
        for row in rows:
            cache.set(f"traffic:latest:{row['link_id']}", json.dumps(row))

    logger.info("traffic: done %s (cached: snapshot=%s incremental=%s)", stats, snapshot, incremental)
    return stats
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/pipelines/test_traffic_cache.py tests/pipelines/test_traffic.py -q`
Expected: PASS（新 3 + 原有全部）。再跑全量 `python3 -m pytest -q`。

- [ ] **Step 5: 提交**

```bash
git add amap_service/pipelines/traffic.py tests/pipelines/test_traffic_cache.py
git commit -m "feat(pipelines): optional cache for traffic (incremental + snapshot)"
```

---

## Task 3：调度器装配 build_scheduler

**Files:**
- Create: `amap_service/scheduler/__init__.py`（空）
- Create: `amap_service/scheduler/runner.py`
- Test: `tests/scheduler/__init__.py`（空）、`tests/scheduler/test_runner.py`

- [ ] **Step 1: 写失败测试**

`tests/scheduler/test_runner.py`:
```python
from amap_service.config.schema import AppConfig
from amap_service.cache.client import NoOpCache
from amap_service.scheduler.runner import build_scheduler


def _config(**overrides):
    data = {
        "amap": {
            "endpoint": "http://192.168.102.102:8080",
            "jobs": {
                "road_network": {"path": "/road", "cron": "0 1 * * *"},
                "traffic_status": {"path": "/traffic", "cron": "*/2 * * * *"},
            },
        },
        "transit": {"username": "u", "password": "p",
                    "token_url": "http://t", "line_list_url": "http://l", "line_entity_url": "http://e"},
    }
    data.update(overrides)
    return AppConfig.model_validate(data)


def test_builds_jobs_for_enabled_amap_jobs():
    sched = build_scheduler(_config(), engine=object(), http_client=object(), cache=NoOpCache())
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"road_network", "traffic_status"}


def test_disabled_job_not_scheduled():
    cfg = _config()
    cfg.amap.jobs.traffic_status.enabled = False
    sched = build_scheduler(cfg, engine=object(), http_client=object(), cache=NoOpCache())
    assert {j.id for j in sched.get_jobs()} == {"road_network"}


def test_cron_trigger_applied():
    sched = build_scheduler(_config(), engine=object(), http_client=object(), cache=NoOpCache())
    job = sched.get_job("traffic_status")
    # APScheduler CronTrigger stringifies its fields; minute field is */2
    assert "minute='*/2'" in str(job.trigger)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/scheduler/test_runner.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/scheduler/__init__.py`: 空文件
`tests/scheduler/__init__.py`: 空文件

`amap_service/scheduler/runner.py`:
```python
"""Assemble a cron-driven scheduler from config, wiring the requirement-1 pipelines."""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from amap_service.pipelines.road_network import run_road_network
from amap_service.pipelines.traffic import run_traffic

logger = logging.getLogger(__name__)


def build_scheduler(config, engine, http_client, cache) -> BlockingScheduler:
    """Register an APScheduler job per enabled amap job. Returns an unstarted scheduler."""
    sched = BlockingScheduler()
    amap = config.amap

    rn = amap.jobs.road_network
    if rn.enabled:
        sched.add_job(
            lambda: run_road_network(engine, http_client, amap.endpoint, rn.path, rn.parse_mode),
            CronTrigger.from_crontab(rn.cron),
            id="road_network", max_instances=1, coalesce=True,
        )

    ts = amap.jobs.traffic_status
    if ts.enabled:
        uses = config.redis.uses
        sched.add_job(
            lambda: run_traffic(
                engine, http_client, amap.endpoint, ts.path, ts.parse_mode,
                cache=cache, snapshot=uses.latest_traffic_snapshot, incremental=uses.incremental_detection,
            ),
            CronTrigger.from_crontab(ts.cron),
            id="traffic_status", max_instances=1, coalesce=True,
        )

    return sched
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/scheduler/test_runner.py -q`
Expected: PASS（3 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/scheduler/__init__.py amap_service/scheduler/runner.py tests/scheduler/__init__.py tests/scheduler/test_runner.py
git commit -m "feat(scheduler): build_scheduler wiring cron jobs to pipelines"
```

---

## Task 4：CLI（initdb / run-once / run）

**Files:**
- Create: `amap_service/cli.py`
- Modify: `pyproject.toml`（在 `[project.optional-dependencies]` 之前或之后增加 `[project.scripts]`）
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`:
```python
import textwrap
from sqlalchemy import inspect
import amap_service.cli as cli
from amap_service.db.engine import make_engine
from amap_service.config.loader import load_config

CONFIG_TMPL = """
amap:
  endpoint: "http://192.168.102.102:8080"
  jobs:
    road_network: {{path: "/road", cron: "0 1 * * *"}}
    traffic_status: {{path: "/traffic", cron: "*/2 * * * *"}}
transit:
  username: u
  password: p
  token_url: http://t
  line_list_url: http://l
  line_entity_url: http://e
database:
  type: sqlite
  sqlite: {{path: "{db}"}}
"""


def _write_config(tmp_path):
    db = tmp_path / "road.db"
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(CONFIG_TMPL.format(db=str(db))), encoding="utf-8")
    return p, db


def test_cmd_initdb_creates_tables(tmp_path):
    cfg_path, db = _write_config(tmp_path)
    cli.main(["initdb", "-c", str(cfg_path)])
    engine = make_engine(load_config(cfg_path).database)
    tables = set(inspect(engine).get_table_names())
    assert {"road_link", "road_link_coord", "traffic_status", "transit_line_raw"} <= tables


def test_run_once_dispatches_road_network(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    calls = {}
    def fake_rn(engine, client, endpoint, path, parse_mode):
        calls.update(endpoint=endpoint, path=path, parse_mode=parse_mode)
        return {"inserted": 0}
    monkeypatch.setattr(cli, "run_road_network", fake_rn)
    cli.main(["run-once", "road-network", "-c", str(cfg_path)])
    assert calls == {"endpoint": "http://192.168.102.102:8080", "path": "/road", "parse_mode": "memory"}


def test_run_once_dispatches_traffic(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_traffic(engine, client, endpoint, path, parse_mode, cache=None, snapshot=False, incremental=False):
        seen.update(path=path, has_cache=cache is not None)
        return {"inserted": 0}
    monkeypatch.setattr(cli, "run_traffic", fake_traffic)
    cli.main(["run-once", "traffic", "-c", str(cfg_path)])
    assert seen["path"] == "/traffic" and seen["has_cache"] is True


def test_run_builds_and_starts_scheduler(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    started = {"n": 0}
    class FakeSched:
        def get_jobs(self): return []
        def start(self): started["n"] += 1
    monkeypatch.setattr(cli, "build_scheduler", lambda *a, **k: FakeSched())
    cli.main(["run", "-c", str(cfg_path)])
    assert started["n"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: FAIL — import error for `amap_service.cli`

- [ ] **Step 3: 实现 + pyproject 脚本入口**

在 `pyproject.toml` 增加（紧跟 `[project]` 段之后即可）：
```toml
[project.scripts]
amap-service = "amap_service.cli:main"
```

`amap_service/cli.py`:
```python
"""Command-line entry: initdb / run-once <job> / run (daemon)."""
import argparse
import logging
from typing import Optional

from amap_service.cache.client import make_cache
from amap_service.clients.base import HttpClient
from amap_service.config.loader import load_config
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.pipelines.road_network import run_road_network
from amap_service.pipelines.traffic import run_traffic
from amap_service.scheduler.runner import build_scheduler

logger = logging.getLogger(__name__)


def _configure_logging(config) -> None:
    logging.basicConfig(level=getattr(logging, config.logging.level.upper(), logging.INFO))


def _build(config):
    engine = make_engine(config.database)
    client = HttpClient(
        timeout_seconds=config.http.timeout_seconds,
        max_retries=config.http.max_retries,
        backoff_seconds=config.http.backoff_seconds,
        headers=config.amap.auth.headers,
    )
    cache = make_cache(config.redis)
    return engine, client, cache


def cmd_initdb(config_path: str) -> None:
    config = load_config(config_path)
    _configure_logging(config)
    init_db(make_engine(config.database))
    logger.info("initdb: tables ensured")


def cmd_run_once(config_path: str, job: str) -> dict:
    config = load_config(config_path)
    _configure_logging(config)
    engine, client, cache = _build(config)
    init_db(engine)
    amap = config.amap
    if job == "road-network":
        rn = amap.jobs.road_network
        return run_road_network(engine, client, amap.endpoint, rn.path, rn.parse_mode)
    if job == "traffic":
        ts = amap.jobs.traffic_status
        uses = config.redis.uses
        return run_traffic(
            engine, client, amap.endpoint, ts.path, ts.parse_mode,
            cache=cache, snapshot=uses.latest_traffic_snapshot, incremental=uses.incremental_detection,
        )
    raise SystemExit(f"unknown job: {job}")


def cmd_run(config_path: str) -> None:
    config = load_config(config_path)
    _configure_logging(config)
    engine, client, cache = _build(config)
    init_db(engine)
    sched = build_scheduler(config, engine, client, cache)
    logger.info("scheduler starting with jobs: %s", [j.id for j in sched.get_jobs()])
    sched.start()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="amap-service")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("initdb", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("-c", "--config", default="config/config.yaml")
    ro = sub.add_parser("run-once")
    ro.add_argument("job", choices=["road-network", "traffic"])
    ro.add_argument("-c", "--config", default="config/config.yaml")

    args = parser.parse_args(argv)
    if args.cmd == "initdb":
        cmd_initdb(args.config)
    elif args.cmd == "run-once":
        cmd_run_once(args.config, args.job)
    elif args.cmd == "run":
        cmd_run(args.config)


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: PASS（4 passed）。再跑全量 `python3 -m pytest -q`（全绿即可，不硬卡数字）。

- [ ] **Step 5: 提交**

```bash
git add amap_service/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat(cli): initdb/run-once/run entrypoints and console script"
```

---

## 完成标准（Definition of Done）

- `python3 -m pytest -q` 全绿。
- `make_cache` 据 `redis.enabled` 返回 NoOp 或 Redis；接口统一，业务无 `if enabled` 分支。
- 路况增量检测跳过未变 link、最新快照写入缓存（fakeredis 测试覆盖）；无缓存时流式路径行为不变。
- `build_scheduler` 据配置注册 enabled job，cron 正确，`max_instances=1`+`coalesce=True`。
- CLI `initdb` 建表、`run-once road-network|traffic` 分发到对应流水线、`run` 装配并启动调度器（测试覆盖，`run` 用 monkeypatch 验证 start 调用）。
- console script `amap-service` 已注册。

## 已知简化（记录，不阻塞）

- 增量/快照对 833k 链路逐键 `get`/`set` 会产生大量 Redis 往返；本计划重正确性。若每 2 分钟周期出现性能瓶颈，后续用 redis pipeline / mget 批量化（单独 task）。
- 缓存启用时 `run_traffic` 物化为 list（牺牲流式）；路况响应相对路网小得多，可接受。
- `cmd_run` 为阻塞式（BlockingScheduler.start）；进程级信号/优雅停机（SIGTERM）未实现，留作部署增强。
- token 缓存（redis.uses.token_cache）在本计划未使用，留待 Plan 5 需求2 接入。

## 后续 Plan

- **Plan 5 — 需求2 阶段一**：transit client（MD5 签名 + token 缓存，复用本计划的 cache）+ token→线路列表→线路对象 链路 + 原始响应存档到 `transit_line_raw` 与 `logs/transit_raw/`。阶段二（字段映射 + transit_segment + 接 SDK）待用户回传真实响应。
