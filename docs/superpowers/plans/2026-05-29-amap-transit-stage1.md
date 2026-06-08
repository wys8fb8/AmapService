# 高德地图数据服务 — Plan 5：需求2 公交线路加工（阶段一：链路 + 签名 + 原始响应存档）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **Commit 约定：** commit message 末尾追加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`，用 `git -c user.name='Claude' -c user.email='noreply@anthropic.com' commit`。

**Goal:** 实现需求2 的**阶段一**：精确移植 token 签名（MD5），打通 `token → 线路列表 → 逐条线路对象` 调用链路，并把**每个接口的原始响应完整存档**（写入 `logs/transit_raw/<name>_<ts>.json` 文件 + `transit_line_raw` 表），供用户运行后回传真实结构。对**未知响应结构**优雅降级：token / 线路名提取使用可配置 dot-path（`transit.token_path` / `transit.line_name_path`）+ 启发式回退；提取不到时记录告警并停在已存档处，不报错。

**为什么是两阶段：** 三个公交接口的响应不在抓包日志中，字段结构未知。阶段一让用户实跑一次拿到真实 JSON；拿到后再做**阶段二**（字段映射 → 调需求3 SDK → 写 `transit_segment`），不在本计划内。

**Architecture:** `clients/transit.py` 提供纯函数 `build_signature`/`build_token_body`（与 note.md .NET 公式逐字一致）+ `TransitClient`（POST token / GET 线路列表 / GET 线路对象；token 缓存：内存或可选 Redis；**capture-first：不 raise_for_status，错误响应也返回以便存档**）。`parsing/transit.py` 提供 `extract_token`/`extract_line_names`（dot-path + 启发式）。`db/repositories.py` 增 `insert_transit_line_raw`。`pipelines/transit.py` 的 `run_transit_stage1` 编排链路 + 存档 + 降级。CLI 增 `run-once transit` 作为捕获入口。

**Tech Stack:** 标准库 `hashlib`/`pathlib`/`json`/`time`、httpx（MockTransport 测试）、复用 Plan 1–4 全部模块、pytest。

**对应 spec：** [设计文档](../specs/2026-05-29-amap-service-design.md) 第 6.3 节 + 5.3 节（`transit_line_raw`）。**前置：** Plan 1–4（已合并 main）。

**签名约定（note.md 权威，已用测试向量钉死）：**
- `ts` = Unix 纪元毫秒；`unsign = "appsecret{pwd}appkey{user}timestamp{ts}appsecret{pwd}"`；`sign = md5(unsign).hexdigest()`（小写十六进制）；body = `"appkey={user}&sign={sign}&timestamp={ts}"`。
- 测试向量：`user=yangs, pwd=pw, ts=1700000000000` → `sign=c07e2485baf739f80c6d2c4ce952f383`，body=`appkey=yangs&sign=c07e2485baf739f80c6d2c4ce952f383&timestamp=1700000000000`。

**结构未知的处理（阶段一关键）：**
- token 在哪个字段、线路列表如何取线路名、token 如何随后续请求传递——**全部未知**。
- token/线路名提取：优先用 `transit.token_path`/`transit.line_name_path`（dot-path，如 `"data.token"`）；未配置则启发式尝试常见键；都失败 → 返回 None / 空列表，pipeline 记告警并停在已存档处。
- token 透传：阶段一暂以 `Authorization` 头携带（provisional）；真实方式待用户回传后在阶段二修正。
- capture-first：HTTP 非 2xx 也返回响应体存档（错误体同样有助于发现结构），仅记录状态码。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `amap_service/config/schema.py` | （修改）`TransitConfig` 增 `token_path`/`line_name_path`/`token_ttl_seconds` |
| `amap_service/parsing/transit.py` | `extract_token` / `extract_line_names`（dot-path + 启发式） |
| `amap_service/db/repositories.py` | （修改）增 `insert_transit_line_raw` |
| `amap_service/clients/transit.py` | `build_signature` / `build_token_body` / `TransitClient` |
| `amap_service/pipelines/transit.py` | `run_transit_stage1`（链路 + 存档 + 降级） |
| `amap_service/cli.py` | （修改）`run-once transit` 入口 |
| `tests/...` | 对应测试 |

---

## Task 1：TransitConfig 增加阶段一字段

**Files:**
- Modify: `amap_service/config/schema.py`（`TransitConfig` 增 3 个可选字段）
- Test: `tests/config/test_schema.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/config/test_schema.py` 末尾追加：
```python
def test_transit_stage1_fields_defaults_and_override():
    cfg = AppConfig.model_validate(_minimal())
    assert cfg.transit.token_path is None
    assert cfg.transit.line_name_path is None
    assert cfg.transit.token_ttl_seconds == 3600

    data = _minimal()
    data["transit"]["token_path"] = "data.token"
    data["transit"]["line_name_path"] = "data"
    data["transit"]["token_ttl_seconds"] = 120
    cfg2 = AppConfig.model_validate(data)
    assert cfg2.transit.token_path == "data.token"
    assert cfg2.transit.line_name_path == "data"
    assert cfg2.transit.token_ttl_seconds == 120
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/config/test_schema.py -q`
Expected: FAIL — 字段不存在

- [ ] **Step 3: 修改 TransitConfig**

在 `amap_service/config/schema.py` 的 `TransitConfig` 增加三个字段（放在 `line_entity_url` 之后、`@field_validator` 之前）：
```python
class TransitConfig(BaseModel):
    enabled: bool = True
    cron: str = "0 3 * * *"
    username: str
    password: str
    token_url: str
    line_list_url: str
    line_entity_url: str
    token_path: Optional[str] = None       # dot-path to token in the (unknown) response; None = heuristic
    line_name_path: Optional[str] = None   # dot-path to the line-name list; None = heuristic
    token_ttl_seconds: int = 3600

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)
```
（`Optional` 已在文件顶部导入。）

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/config/test_schema.py -q`
Expected: PASS。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/config/schema.py tests/config/test_schema.py
git commit -m "feat(config): transit stage-1 fields (token_path/line_name_path/token_ttl)"
```

---

## Task 2：提取助手 parsing/transit.py

**Files:**
- Create: `amap_service/parsing/transit.py`
- Test: `tests/parsing/test_transit.py`

- [ ] **Step 1: 写失败测试**

`tests/parsing/test_transit.py`:
```python
from amap_service.parsing.transit import extract_token, extract_line_names


def test_extract_token_explicit_path():
    raw = {"data": {"token": "T123"}}
    assert extract_token(raw, "data.token") == "T123"


def test_extract_token_heuristic_top_level():
    assert extract_token({"token": "X"}) == "X"
    assert extract_token({"accessToken": "Y"}) == "Y"


def test_extract_token_heuristic_nested():
    assert extract_token({"data": {"accessToken": "Z"}}) == "Z"
    assert extract_token({"result": {"token": "R"}}) == "R"


def test_extract_token_missing_returns_none():
    assert extract_token({"nope": 1}) is None
    assert extract_token({"data": {"token": "T"}}, "data.missing") is None
    assert extract_token("not-a-dict") is None


def test_extract_line_names_explicit_path_list_of_dicts():
    raw = {"data": [{"lineName": "L1"}, {"lineName": "L2"}]}
    assert extract_line_names(raw, "data") == ["L1", "L2"]


def test_extract_line_names_heuristic_list_of_str():
    assert extract_line_names(["A", "B"]) == ["A", "B"]


def test_extract_line_names_heuristic_container():
    assert extract_line_names({"data": [{"name": "N1"}, {"lineName": "N2"}]}) == ["N1", "N2"]


def test_extract_line_names_undetermined_empty():
    assert extract_line_names({"weird": 1}) == []
    assert extract_line_names({"data": [{"id": 1}]}) == []   # no name-like key
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/parsing/test_transit.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/parsing/transit.py`:
```python
"""Best-effort extraction of token and line names from the (still-unknown) transit responses.

Explicit dot-path config wins; otherwise common-shape heuristics are tried. Returns None /
[] when undetermined so the stage-1 pipeline can archive raw and stop gracefully.
"""
from typing import Optional

_TOKEN_KEYS = ("token", "accessToken", "access_token", "Token", "AccessToken")
_NAME_KEYS = ("lineName", "name", "LineName", "Name")
_CONTAINERS = ("data", "result", "Data", "Result", "lines", "list")


def _dig(obj, path: str):
    """Navigate a dot-path (e.g. 'data.token' or 'data.0.x') through nested dict/list."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def extract_token(raw, path: Optional[str] = None) -> Optional[str]:
    if path:
        val = _dig(raw, path)
        return str(val) if val is not None else None
    if isinstance(raw, dict):
        for key in _TOKEN_KEYS:
            if raw.get(key) is not None:
                return str(raw[key])
        for container in _CONTAINERS:
            sub = raw.get(container)
            if isinstance(sub, dict):
                for key in _TOKEN_KEYS:
                    if sub.get(key) is not None:
                        return str(sub[key])
    return None


def extract_line_names(raw, path: Optional[str] = None) -> list:
    candidate = _dig(raw, path) if path else None
    if candidate is None:
        if isinstance(raw, list):
            candidate = raw
        elif isinstance(raw, dict):
            for container in _CONTAINERS:
                if isinstance(raw.get(container), list):
                    candidate = raw[container]
                    break
    if not isinstance(candidate, list):
        return []
    names = []
    for item in candidate:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            for key in _NAME_KEYS:
                if item.get(key):
                    names.append(str(item[key]))
                    break
    return names
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/parsing/test_transit.py -q`
Expected: PASS（8 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/parsing/transit.py tests/parsing/test_transit.py
git commit -m "feat(parsing): transit token/line-name extraction (path + heuristics)"
```

---

## Task 3：原始响应入库 insert_transit_line_raw

**Files:**
- Modify: `amap_service/db/repositories.py`（追加函数 + import）
- Test: `tests/db/test_transit_raw.py`

- [ ] **Step 1: 写失败测试**

`tests/db/test_transit_raw.py`:
```python
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_line_raw
from amap_service.db.repositories import insert_transit_line_raw


def test_insert_transit_line_raw(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    insert_transit_line_raw(e, "token", '{"data":{"token":"T"}}')
    insert_transit_line_raw(e, "line_list", '{"data":[]}')
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 2
        row = c.execute(
            select(transit_line_raw.c.line_name, transit_line_raw.c.raw_response)
            .where(transit_line_raw.c.line_name == "token")
        ).one()
        assert row.line_name == "token" and "T" in row.raw_response
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/db/test_transit_raw.py -q`
Expected: FAIL — `insert_transit_line_raw` 不存在

- [ ] **Step 3: 实现**

在 `amap_service/db/repositories.py`：把 import 行 `from .schema import road_link, road_link_coord, traffic_status` 改为 `from .schema import road_link, road_link_coord, traffic_status, transit_line_raw`，并在文件末尾追加：
```python
def insert_transit_line_raw(engine: Engine, line_name: str, raw_response) -> None:
    """Archive one upstream transit response (stage-1 capture) into transit_line_raw."""
    with engine.begin() as conn:
        conn.execute(
            transit_line_raw.insert().values(line_name=line_name, raw_response=raw_response)
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/db/test_transit_raw.py -q`
Expected: PASS。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/db/repositories.py tests/db/test_transit_raw.py
git commit -m "feat(db): insert_transit_line_raw for stage-1 archival"
```

---

## Task 4：TransitClient（签名 + 链路 + token 缓存）

**Files:**
- Create: `amap_service/clients/transit.py`
- Test: `tests/clients/test_transit.py`

- [ ] **Step 1: 写失败测试**

`tests/clients/test_transit.py`:
```python
import httpx
import fakeredis
from amap_service.config.schema import TransitConfig
from amap_service.cache.client import RedisCache
from amap_service.clients.transit import build_signature, build_token_body, TransitClient


def _cfg(**kw):
    base = dict(username="yangs", password="pw",
                token_url="http://h/token", line_list_url="http://h/list",
                line_entity_url="http://h/entity", token_path="data.token")
    base.update(kw)
    return TransitConfig(**base)


def test_signature_matches_note_md_formula():
    # MD5("appsecretpwappkeyyangstimestamp1700000000000appsecretpw")
    assert build_signature("yangs", "pw", 1700000000000) == "c07e2485baf739f80c6d2c4ce952f383"


def test_token_body_format():
    body = build_token_body("yangs", "pw", 1700000000000)
    assert body == "appkey=yangs&sign=c07e2485baf739f80c6d2c4ce952f383&timestamp=1700000000000"


def test_get_token_posts_signed_body_and_extracts():
    seen = {}
    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1700000000000)
    token, raw = client.get_token()
    assert token == "TOK"
    assert seen["method"] == "POST" and seen["url"] == "http://h/token"
    assert seen["body"] == "appkey=yangs&sign=c07e2485baf739f80c6d2c4ce952f383&timestamp=1700000000000"
    assert "TOK" in raw
    client.close()


def test_get_token_memory_cached_second_call_no_request():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    t1, _ = client.get_token()
    t2, raw2 = client.get_token()
    assert t1 == "TOK" and t2 == "TOK"
    assert calls["n"] == 1 and raw2 is None     # served from memory cache, no 2nd POST, no raw
    client.close()


def test_get_token_redis_cached():
    cache = RedisCache(fakeredis.FakeRedis())
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"token": "TOK"}})
    c1 = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1,
                       cache=cache, token_cache_enabled=True)
    c1.get_token()
    # a fresh client sharing the same cache must not POST again
    c2 = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1,
                       cache=cache, token_cache_enabled=True)
    token, raw = c2.get_token()
    assert token == "TOK" and raw is None and calls["n"] == 1
    c1.close(); c2.close()


def test_get_line_list_and_entity_capture_first_on_error():
    def handler(request):
        if request.url.path == "/list":
            assert request.url.params.get("loginname") == "yangs"
            return httpx.Response(500, text="boom")          # capture-first: error body returned
        assert request.url.params.get("lineName") == "L1"
        return httpx.Response(200, text='{"entity": 1}')
    client = TransitClient(_cfg(), transport=httpx.MockTransport(handler), now_ms=lambda: 1)
    assert client.get_line_list("TOK") == "boom"             # no raise on 500
    assert client.get_line_entity("TOK", "L1") == '{"entity": 1}'
    client.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/clients/test_transit.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/clients/transit.py`:
```python
"""Transit (bus-line) upstream client: signed token, line list, line entity.

Capture-first: methods do NOT raise on non-2xx — the response body is returned so the
stage-1 pipeline can archive even error responses (useful while the schema is unknown).
"""
import hashlib
import logging
import time
from typing import Optional

import httpx

from amap_service.parsing.transit import extract_token

logger = logging.getLogger(__name__)

_TOKEN_CACHE_KEY = "transit:token"


def build_signature(username: str, password: str, ts: int) -> str:
    """MD5('appsecret{pwd}appkey{user}timestamp{ts}appsecret{pwd}'), lowercase hex (note.md)."""
    unsign = f"appsecret{password}appkey{username}timestamp{ts}appsecret{password}"
    return hashlib.md5(unsign.encode()).hexdigest()


def build_token_body(username: str, password: str, ts: int) -> str:
    sign = build_signature(username, password, ts)
    return f"appkey={username}&sign={sign}&timestamp={ts}"


class TransitClient:
    def __init__(self, config_transit, *, transport=None, timeout: float = 30.0,
                 cache=None, token_cache_enabled: bool = False, now_ms=None):
        self._t = config_transit
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._cache = cache
        self._token_cache_enabled = token_cache_enabled
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._mem_token: Optional[str] = None

    def _redis_enabled(self) -> bool:
        return self._token_cache_enabled and self._cache is not None and getattr(self._cache, "enabled", False)

    def get_token(self):
        """Return (token, raw_text). raw_text is None when served from cache (no request made)."""
        if self._redis_enabled():
            cached = self._cache.get(_TOKEN_CACHE_KEY)
            if cached:
                return cached, None
        elif self._mem_token:
            return self._mem_token, None

        ts = self._now_ms()
        body = build_token_body(self._t.username, self._t.password, ts)
        resp = self._client.post(
            self._t.token_url, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raw_text = resp.text
        if resp.status_code >= 300:
            logger.warning("transit get_token: HTTP %s", resp.status_code)
        token = None
        try:
            token = extract_token(resp.json(), self._t.token_path)
        except Exception:  # noqa: BLE001 - unknown body may not be JSON
            token = None
        if token:
            if self._redis_enabled():
                self._cache.set(_TOKEN_CACHE_KEY, token, ttl=self._t.token_ttl_seconds)
            else:
                self._mem_token = token
        return token, raw_text

    def _auth_headers(self, token: Optional[str]) -> dict:
        # provisional: real token-passing scheme is unknown until stage-2 (see plan)
        return {"Authorization": token} if token else {}

    def get_line_list(self, token: Optional[str]) -> str:
        resp = self._client.get(
            self._t.line_list_url, params={"loginname": self._t.username},
            headers=self._auth_headers(token),
        )
        if resp.status_code >= 300:
            logger.warning("transit get_line_list: HTTP %s", resp.status_code)
        return resp.text

    def get_line_entity(self, token: Optional[str], line_name: str) -> str:
        resp = self._client.get(
            self._t.line_entity_url, params={"lineName": line_name},
            headers=self._auth_headers(token),
        )
        if resp.status_code >= 300:
            logger.warning("transit get_line_entity(%s): HTTP %s", line_name, resp.status_code)
        return resp.text

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/clients/test_transit.py -q`
Expected: PASS（6 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/clients/transit.py tests/clients/test_transit.py
git commit -m "feat(clients): TransitClient with MD5 signature, chain calls, token cache"
```

---

## Task 5：阶段一流水线 run_transit_stage1

**Files:**
- Create: `amap_service/pipelines/transit.py`
- Test: `tests/pipelines/test_transit.py`

- [ ] **Step 1: 写失败测试**

`tests/pipelines/test_transit.py`:
```python
import json
import httpx
from sqlalchemy import func, select
from amap_service.config.schema import AppConfig, DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import transit_line_raw
from amap_service.clients.transit import TransitClient
from amap_service.pipelines.transit import run_transit_stage1


def _config(token_path="data.token", line_name_path="data"):
    return AppConfig.model_validate({
        "amap": {"endpoint": "http://h", "jobs": {
            "road_network": {"path": "/r", "cron": "0 1 * * *"},
            "traffic_status": {"path": "/t", "cron": "*/2 * * * *"}}},
        "transit": {"username": "yangs", "password": "pw",
                    "token_url": "http://h/token", "line_list_url": "http://h/list",
                    "line_entity_url": "http://h/entity",
                    "token_path": token_path, "line_name_path": line_name_path},
    })


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client(handler):
    return TransitClient(_config().transit, transport=httpx.MockTransport(handler), now_ms=lambda: 1)


def test_full_chain_archives_token_list_entities(tmp_path):
    def handler(request):
        p = request.url.path
        if p == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        if p == "/list":
            return httpx.Response(200, json={"data": [{"lineName": "L1"}, {"lineName": "L2"}]})
        return httpx.Response(200, text=json.dumps({"line": request.url.params.get("lineName")}))

    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 111)
    assert stats == {"token_ok": True, "line_count": 2, "entities_archived": 2}
    with e.connect() as c:
        # token + line_list + 2 entities = 4 archived rows
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 4
    # files written
    written = {p.name for p in out.iterdir()}
    assert "token_111.json" in written and "line_list_111.json" in written
    assert "line_entity_L1_111.json" in written and "line_entity_L2_111.json" in written


def test_degrade_when_token_not_extracted(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    # token_path points nowhere; heuristic also fails → token_ok False, only token archived
    stats = run_transit_stage1(e, _client(handler), _config(token_path="data.token"),
                               out_dir=str(out), now_ms=lambda: 5)
    assert stats == {"token_ok": False, "line_count": 0, "entities_archived": 0}
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 1  # token only


def test_degrade_when_no_line_names(tmp_path):
    def handler(request):
        if request.url.path == "/token":
            return httpx.Response(200, json={"data": {"token": "TOK"}})
        return httpx.Response(200, json={"data": []})   # empty list → no names
    e = _engine(tmp_path)
    out = tmp_path / "raw"
    stats = run_transit_stage1(e, _client(handler), _config(), out_dir=str(out), now_ms=lambda: 7)
    assert stats == {"token_ok": True, "line_count": 0, "entities_archived": 0}
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(transit_line_raw)).scalar() == 2  # token + list
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/pipelines/test_transit.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/pipelines/transit.py`:
```python
"""Requirement-2 STAGE 1: walk token → line list → line entities, archiving every raw response.

Field mapping (→ ordered segments via the requirement-3 SDK) is STAGE 2, done once the user
returns the real response structures. Stage 1 degrades gracefully when token / line names
cannot be extracted: it archives what it has and stops without error.
"""
import json
import logging
import time
from pathlib import Path

from sqlalchemy import Engine

from amap_service.clients.transit import TransitClient
from amap_service.db.repositories import insert_transit_line_raw
from amap_service.parsing.transit import extract_line_names

logger = logging.getLogger(__name__)


def _archive(engine: Engine, out_dir: str, name: str, raw_text, ts: int) -> None:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_")
    (directory / f"{safe}_{ts}.json").write_text(raw_text or "", encoding="utf-8")
    insert_transit_line_raw(engine, name, raw_text)


def run_transit_stage1(engine: Engine, transit_client: TransitClient, config,
                       out_dir: str = "logs/transit_raw", now_ms=None) -> dict:
    now = now_ms or (lambda: int(time.time() * 1000))
    ts = now()
    stats = {"token_ok": False, "line_count": 0, "entities_archived": 0}

    token, raw_token = transit_client.get_token()
    if raw_token is not None:
        _archive(engine, out_dir, "token", raw_token, ts)
    if not token:
        logger.warning(
            "transit stage1: token not extracted; set transit.token_path after inspecting the "
            "archived token response. Stopping after token archival."
        )
        return stats
    stats["token_ok"] = True

    raw_list = transit_client.get_line_list(token)
    _archive(engine, out_dir, "line_list", raw_list, ts)
    try:
        line_names = extract_line_names(json.loads(raw_list), config.transit.line_name_path)
    except Exception:  # noqa: BLE001 - unknown body may not be JSON
        line_names = []
    stats["line_count"] = len(line_names)
    if not line_names:
        logger.warning(
            "transit stage1: no line names extracted; set transit.line_name_path after inspecting "
            "the archived line_list response. token + line_list archived; stopping."
        )
        return stats

    for name in line_names:
        raw_entity = transit_client.get_line_entity(token, name)
        _archive(engine, out_dir, f"line_entity_{name}", raw_entity, ts)
        stats["entities_archived"] += 1

    logger.info("transit stage1: archived token + line_list + %d entities", stats["entities_archived"])
    return stats
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/pipelines/test_transit.py -q`
Expected: PASS（3 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/pipelines/transit.py tests/pipelines/test_transit.py
git commit -m "feat(pipelines): transit stage-1 chain with raw-response archival"
```

---

## Task 6：CLI 接入 `run-once transit`

**Files:**
- Modify: `amap_service/cli.py`（顶部 import TransitClient/run_transit_stage1；run-once 增 `transit`）
- Test: `tests/test_cli.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_cli.py` 末尾追加：
```python
def test_run_once_dispatches_transit(tmp_path, monkeypatch):
    cfg_path, _ = _write_config(tmp_path)
    seen = {}
    def fake_stage1(engine, transit_client, config, **kw):
        seen["called"] = True
        seen["has_client"] = transit_client is not None
        return {"token_ok": False, "line_count": 0, "entities_archived": 0}
    monkeypatch.setattr(cli, "run_transit_stage1", fake_stage1)
    cli.main(["run-once", "transit", "-c", str(cfg_path)])
    assert seen == {"called": True, "has_client": True}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_cli.py::test_run_once_dispatches_transit -q`
Expected: FAIL — `run-once` 不接受 `transit`（argparse choices）/ `run_transit_stage1` 不在 cli 命名空间

- [ ] **Step 3: 修改 cli.py**

1）在 import 区追加：
```python
from amap_service.clients.transit import TransitClient
from amap_service.pipelines.transit import run_transit_stage1
```
2）在 `cmd_run_once` 的 `if job == "traffic":` 分支之后、`raise SystemExit(...)` 之前，增加：
```python
    if job == "transit":
        tc = TransitClient(
            config.transit,
            timeout=config.http.timeout_seconds,
            cache=cache,
            token_cache_enabled=config.redis.uses.token_cache,
        )
        return run_transit_stage1(engine, tc, config)
```
3）把 run-once 的 `choices` 改为包含 transit：
```python
    ro.add_argument("job", choices=["road-network", "traffic", "transit"])
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: PASS。再跑全量 `python3 -m pytest -q`（全绿即可）。

- [ ] **Step 5: 提交**

```bash
git add amap_service/cli.py tests/test_cli.py
git commit -m "feat(cli): run-once transit entrypoint for stage-1 capture"
```

---

## 完成标准（Definition of Done）

- `python3 -m pytest -q` 全绿。
- 签名与 body 与 note.md 公式逐字一致（测试向量 `c07e2485...` 钉死）。
- `run-once transit` 能跑通链路：token → 线路列表 → 逐条线路对象，并把每个原始响应存到 `logs/transit_raw/*.json` 与 `transit_line_raw` 表。
- 对未知结构优雅降级：token / 线路名提取不到时记告警、存档已得部分、返回统计、不抛错。
- token 缓存（内存 / 可选 Redis）经测试覆盖。

## 阶段二（待用户回传真实响应后，单独计划）

- 依真实 JSON 确定 `token_path`、`line_name_path`、token 透传方式（header/param）。
- 设计 `transit_segment` 表结构；从线路对象提取 GPS 轨迹 → 调 `TrackConverter.linetrack_to_linkinfos` → 写有序路段（含 `reverse_coords`）。
- 把 transit 纳入 `build_scheduler`（按 `transit.cron`）并接入需求3 SDK（届时 `SdkConfig` 的 `match_tolerance_m`/`reverse_angle_deg` 由此处桥接到 `TrackConverter`）。

## 已知简化（记录，不阻塞）

- token 透传方式为 provisional（`Authorization` 头）；真实方式待阶段二确认。
- capture-first 不 raise：非 2xx 也存档（错误体有助发现结构）；仅记日志。
- 线路对象逐条串行 GET；线路多时可在阶段二并发化。
- transit 暂不进 cron 调度（阶段一以 `run-once` 捕获为主）；阶段二再纳入 `build_scheduler`。
