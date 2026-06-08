# 高德地图数据服务 — Plan 3：需求1 数据落地流水线（路网 + 实时路况）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **Commit 约定：** commit message 末尾追加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`，用 `git -c user.name='Claude' -c user.email='noreply@anthropic.com' commit`。

**Goal:** 实现需求1 的两条落地流水线：从上游 `areaLinkPub`（全量路网）与 `traffic/status`（实时路况）拉取 JSON → 解析（coordList 成对、64 位 linkId、listSectionStatus 分段聚合）→ 经 Plan 1 的 repositories upsert 入库。**解析支持两种模式、配置驱动**：`memory`（一次性 `json.loads`，可重试，代码简单）与 `stream`（ijson 边下边解析，内存恒定，适合 408k links 的大响应）。

**Architecture:** `clients/base.py` 的 `HttpClient` 同时提供 `get_json`（一次性，含重试）与 `stream_items`（流式，ijson 增量解析，经 `_BytesIterReader` 把 httpx 字节流适配成 file-like）。`parsing/` 为纯函数，拆成**逐条目函数**（`*_item`，供流式逐条调用）+ **payload 函数**（供 memory 模式映射）；统一把坐标规范化为 `float`（ijson 对小数产出 `Decimal`，SQLite 无法绑定，必须转 float）。`pipelines/` 按每 job 的 `parse_mode` 选择两条路径之一，再 upsert。

**Tech Stack:** Python 3.11、httpx 0.28（`MockTransport` 测试，无需新依赖）、ijson 3.5（已装，pyproject 已列）、复用 Plan 1 `db.repositories`/`engine`/`migrate`、pytest。

**对应 spec：** [设计文档](../specs/2026-05-29-amap-service-design.md) 第 5、6 节。**前置：** Plan 1 + Plan 2（已合并 main）。

**关键约定（来自真实日志 + 流式探针实测）：**
- 路网无外层信封：`payload["linkCoordList"]` 直接是数组。`coordList` 扁平 `[lng,lat,lng,lat,...]`，**经度在前**；两两成对 `(lng,lat)`，序列化 `line_track="lng,lat;lng,lat"`；奇数长度丢弃末尾未配对项。
- 路况 `payload["linkStates"]` 每项要么顶层 `speed/state/travelTime`，要么 `listSectionStatus`（分段）。
- 分段聚合：**speed = travelTime 加权平均**（全 0 时退化算术平均）；**travel_time = 各段 travelTime 之和**（和为 0 → None）；**state = 最拥堵段**（拥堵序 4>3>2>1，`5=未知` 最低优先，仅全为 5 时取 5）。
- **数值规范化：** `link_id`/`length`/`formway`/`roadclass`/`speed`/`state`/`travelTime` 在两种模式下都是 `int`（无损）；**坐标经纬度统一 `float`**（ijson 流式会产出 `Decimal`，必须在解析层 `float()` 转换，否则 SQLite 绑定失败）。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `amap_service/config/schema.py` | （修改）`JobConfig` 增加 `parse_mode: memory\|stream` |
| `amap_service/clients/__init__.py` / `base.py` | `HttpClient`：`get_json`（重试）+ `stream_items`（ijson）+ `_BytesIterReader` |
| `amap_service/parsing/__init__.py` | 子包标识 |
| `amap_service/parsing/road_network.py` | `parse_road_link_item(item)` + `parse_road_network(payload)` |
| `amap_service/parsing/traffic.py` | `parse_traffic_item(item)` + `parse_traffic(payload)`（分段聚合） |
| `amap_service/pipelines/__init__.py` | 子包标识 |
| `amap_service/pipelines/road_network.py` | `run_road_network(engine, client, endpoint, path, parse_mode)` |
| `amap_service/pipelines/traffic.py` | `run_traffic(engine, client, endpoint, path, parse_mode)` |
| `tests/...` | 对应测试 |

> 结构说明：设计文档曾列 `clients/road_network.py` 等单独 client 文件；此处简化为「单一 `HttpClient` + pipeline 内构造 URL」（YAGNI）。需求2 的 transit 因需签名会在 Plan 5 自带 client。

---

## Task 1：配置增加 parse_mode（按 job 选择解析模式）

**Files:**
- Modify: `amap_service/config/schema.py`（给 `JobConfig` 增加 `parse_mode` 字段）
- Test: `tests/config/test_schema.py`（追加用例）

- [ ] **Step 1: 追加失败测试**

在 `tests/config/test_schema.py` 末尾追加：
```python
def test_job_parse_mode_default_and_values():
    cfg = AppConfig.model_validate(_minimal())
    # default is memory (simple + retryable); large jobs opt into stream via config
    assert cfg.amap.jobs.road_network.parse_mode == "memory"

    data = _minimal()
    data["amap"]["jobs"]["road_network"]["parse_mode"] = "stream"
    cfg2 = AppConfig.model_validate(data)
    assert cfg2.amap.jobs.road_network.parse_mode == "stream"


def test_job_parse_mode_invalid_rejected():
    import pytest
    from pydantic import ValidationError
    data = _minimal()
    data["amap"]["jobs"]["traffic_status"]["parse_mode"] = "nope"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/config/test_schema.py -q`
Expected: FAIL — `parse_mode` 默认不存在 / 不接受该字段

- [ ] **Step 3: 修改 JobConfig**

在 `amap_service/config/schema.py` 的 `JobConfig` 中，`enabled` 字段下方增加 `parse_mode`（`Literal` 已在文件顶部从 typing 导入）：
```python
class JobConfig(BaseModel):
    path: str
    cron: str
    enabled: bool = True
    parse_mode: Literal["memory", "stream"] = "memory"

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/config/test_schema.py -q`
Expected: PASS。再跑全量 `python3 -m pytest -q`。

- [ ] **Step 5: 提交**

```bash
git add amap_service/config/schema.py tests/config/test_schema.py
git commit -m "feat(config): per-job parse_mode (memory|stream)"
```

---

## Task 2：HttpClient（get_json 重试 + stream_items 流式）

**Files:**
- Create: `amap_service/clients/__init__.py`（空）
- Create: `amap_service/clients/base.py`
- Test: `tests/clients/__init__.py`（空）、`tests/clients/test_base.py`

- [ ] **Step 1: 写失败测试**

`tests/clients/test_base.py`:
```python
import httpx
import pytest
from amap_service.clients.base import HttpClient


def _client_with(handler, **kw):
    return HttpClient(timeout_seconds=5, backoff_seconds=0,
                      transport=httpx.MockTransport(handler), **kw)


def test_get_json_success():
    def handler(request):
        assert request.url.path == "/x"
        return httpx.Response(200, json={"linkCoordList": [], "n": 1})
    with _client_with(handler) as c:
        assert c.get_json("http://h/x") == {"linkCoordList": [], "n": 1}


def test_get_json_preserves_bigint():
    def handler(request):
        return httpx.Response(200, content=b'{"linkId": 5130091959790075998}',
                              headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        assert c.get_json("http://h/x")["linkId"] == 5130091959790075998


def test_retries_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})
    with _client_with(handler, max_retries=3) as c:
        assert c.get_json("http://h/x") == {"ok": True}
    assert calls["n"] == 3


def test_gives_up_and_raises():
    def handler(request):
        return httpx.Response(503)
    with _client_with(handler, max_retries=2) as c:
        with pytest.raises(httpx.HTTPError):
            c.get_json("http://h/x")


def test_passes_params_and_headers():
    seen = {}
    def handler(request):
        seen["q"] = request.url.params.get("loginname")
        seen["auth"] = request.headers.get("x-token")
        return httpx.Response(200, json={})
    with _client_with(handler, headers={"x-token": "T"}) as c:
        c.get_json("http://h/x", params={"loginname": "yangs"})
    assert seen == {"q": "yangs", "auth": "T"}


def test_stream_items_yields_each_element():
    payload = (b'{"linkCoordList":[{"linkId":5130091959790075998,"coordList":[120.9,31.0]},'
               b'{"linkId":5130091959790075999,"coordList":[1.0,2.0]}]}')
    def handler(request):
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})
    with _client_with(handler) as c:
        items = list(c.stream_items("http://h/areaLinkPub", "linkCoordList.item"))
    assert len(items) == 2
    assert items[0]["linkId"] == 5130091959790075998   # 64-bit int preserved


def test_stream_items_raises_on_http_error():
    def handler(request):
        return httpx.Response(500)
    with _client_with(handler) as c:
        with pytest.raises(httpx.HTTPError):
            list(c.stream_items("http://h/x", "linkCoordList.item"))
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/clients/test_base.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/clients/__init__.py`: 空文件
`tests/clients/__init__.py`: 空文件

`amap_service/clients/base.py`:
```python
"""HTTP client for upstream JSON APIs.

get_json   — one-shot fetch with timeout + exponential-backoff retry (memory mode).
stream_items — streaming fetch + incremental ijson parse (stream mode); NOT retried,
               since a partially-consumed stream cannot be safely replayed. A failed
               streaming job is simply re-run on its next cron cycle.
"""
import logging
import time
from typing import Iterator, Optional

import httpx
import ijson

logger = logging.getLogger(__name__)


class _BytesIterReader:
    """Adapt an iterator of bytes (httpx iter_bytes) into a read(size) file-like for ijson."""

    def __init__(self, byte_iter):
        self._it = byte_iter
        self._buf = b""

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            chunks.extend(self._it)
            return b"".join(chunks)
        while len(self._buf) < size:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        out, self._buf = self._buf[:size], self._buf[size:]
        return out


class HttpClient:
    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        headers: Optional[dict] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.max_retries = max(1, max_retries)
        self.backoff_seconds = backoff_seconds
        self._client = httpx.Client(timeout=timeout_seconds, headers=headers or {}, transport=transport)

    def get_json(self, url: str, params: Optional[dict] = None):
        """GET + raise_for_status + JSON parse, with retry. Integers stay arbitrary-precision int."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
        assert last_exc is not None
        raise last_exc

    def stream_items(self, url: str, prefix: str, params: Optional[dict] = None) -> Iterator:
        """Stream GET and yield each JSON element at `prefix` (e.g. 'linkCoordList.item').

        Note: ijson yields Decimal for fractional numbers — callers/parsers must normalize.
        """
        with self._client.stream("GET", url, params=params) as resp:
            resp.raise_for_status()
            reader = _BytesIterReader(resp.iter_bytes())
            yield from ijson.items(reader, prefix)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/clients/test_base.py -q`
Expected: PASS（7 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/clients/__init__.py amap_service/clients/base.py tests/clients/__init__.py tests/clients/test_base.py
git commit -m "feat(clients): HttpClient with retrying get_json and streaming stream_items"
```

---

## Task 3：路网解析（逐条目 + payload，坐标规范化 float）

**Files:**
- Create: `amap_service/parsing/__init__.py`（空）
- Create: `amap_service/parsing/road_network.py`
- Test: `tests/parsing/__init__.py`（空）、`tests/parsing/test_road_network.py`

- [ ] **Step 1: 写失败测试**

`tests/parsing/test_road_network.py`:
```python
from decimal import Decimal
from amap_service.parsing.road_network import parse_road_link_item, parse_road_network

ITEM = {
    "linkId": 5130091959790075998,
    "coordList": [120.93746244907379, 31.06035053730011, 120.9343296289444, 31.05913281440735],
    "roadName": "G50沪渝高速", "length": 328, "formway": 1, "roadclass": 0,
}


def test_item_pairs_coords_and_serializes():
    out = parse_road_link_item(ITEM)
    assert out["link_id"] == 5130091959790075998          # 64-bit int
    assert out["road_name"] == "G50沪渝高速"
    assert out["length"] == 328 and out["formway"] == 1 and out["roadclass"] == 0
    assert out["coords"] == [(120.93746244907379, 31.06035053730011),
                             (120.9343296289444, 31.05913281440735)]
    assert out["line_track"] == "120.93746244907379,31.06035053730011;120.9343296289444,31.05913281440735"


def test_item_decimal_coords_normalized_to_float():
    # ijson stream mode yields Decimal for fractional numbers; must become float
    item = {"linkId": 1, "coordList": [Decimal("120.9"), Decimal("31.0")]}
    out = parse_road_link_item(item)
    assert out["coords"] == [(120.9, 31.0)]
    assert all(isinstance(v, float) for pt in out["coords"] for v in pt)
    assert out["line_track"] == "120.9,31.0"


def test_item_odd_coordlist_drops_trailing():
    out = parse_road_link_item({"linkId": 1, "coordList": [1.0, 2.0, 3.0]})
    assert out["coords"] == [(1.0, 2.0)]


def test_item_missing_optionals_default_none():
    out = parse_road_link_item({"linkId": 7, "coordList": []})
    assert out["road_name"] is None and out["length"] is None
    assert out["coords"] == [] and out["line_track"] == ""


def test_parse_payload_maps_items():
    out = list(parse_road_network({"linkCoordList": [ITEM, {"linkId": 2, "coordList": [1.0, 2.0]}]}))
    assert [o["link_id"] for o in out] == [5130091959790075998, 2]


def test_parse_payload_empty():
    assert list(parse_road_network({})) == []
    assert list(parse_road_network({"linkCoordList": []})) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/parsing/test_road_network.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/parsing/__init__.py`: 空文件
`tests/parsing/__init__.py`: 空文件

`amap_service/parsing/road_network.py`:
```python
"""Parse areaLinkPub (full road network) into repository-ready link dicts.

Works for both memory mode (json floats) and stream mode (ijson Decimals):
coordinates are normalized to float so SQLite can bind them.
"""
from typing import Iterator


def parse_road_link_item(item: dict) -> dict:
    """One linkCoordList element → {link_id, road_name, length, formway, roadclass, line_track, coords}."""
    flat = item.get("coordList") or []
    pair_count = len(flat) // 2
    coords = [(float(flat[2 * i]), float(flat[2 * i + 1])) for i in range(pair_count)]
    line_track = ";".join(f"{lng},{lat}" for lng, lat in coords)
    return {
        "link_id": item["linkId"],
        "road_name": item.get("roadName"),
        "length": item.get("length"),
        "formway": item.get("formway"),
        "roadclass": item.get("roadclass"),
        "line_track": line_track,
        "coords": coords,
    }


def parse_road_network(payload: dict) -> Iterator[dict]:
    """Map every linkCoordList element through parse_road_link_item (memory mode)."""
    for item in payload.get("linkCoordList", []):
        yield parse_road_link_item(item)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/parsing/test_road_network.py -q`
Expected: PASS（6 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/parsing/__init__.py amap_service/parsing/road_network.py tests/parsing/__init__.py tests/parsing/test_road_network.py
git commit -m "feat(parsing): road network item/payload parsing with float-normalized coords"
```

---

## Task 4：路况解析（逐条目 + payload，含分段聚合）

**Files:**
- Create: `amap_service/parsing/traffic.py`
- Test: `tests/parsing/test_traffic.py`

- [ ] **Step 1: 写失败测试**

`tests/parsing/test_traffic.py`:
```python
from amap_service.parsing.traffic import parse_traffic_item, parse_traffic


def test_top_level_item():
    out = parse_traffic_item({"linkId": 5130516143645130888, "speed": 89, "state": 1, "travelTime": 59})
    assert out == {"link_id": 5130516143645130888, "speed": 89, "state": 1, "travel_time": 59}


def test_section_weighted_speed_and_sum_tt():
    item = {"linkId": 5130516143645131894, "listSectionStatus": [
        {"offset": 3765, "reliability": 89, "speed": 88, "state": 1, "travelTime": 688},
        {"offset": 2165, "reliability": 89, "speed": 92, "state": 1, "travelTime": 307},
    ]}
    # weighted = round((88*688 + 92*307)/(688+307)) = round(89.23) = 89 ; tt = 995 ; state = 1
    assert parse_traffic_item(item) == {"link_id": 5130516143645131894, "speed": 89, "state": 1, "travel_time": 995}


def test_section_state_most_congested_ignores_unknown():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 30, "state": 1, "travelTime": 10},
        {"speed": 10, "state": 3, "travelTime": 20},
        {"speed": 0,  "state": 5, "travelTime": 5},
    ]}
    out = parse_traffic_item(item)
    assert out["state"] == 3 and out["travel_time"] == 35


def test_section_all_unknown_state_5():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 0, "state": 5, "travelTime": 10}, {"speed": 0, "state": 5, "travelTime": 10},
    ]}
    assert parse_traffic_item(item)["state"] == 5


def test_section_zero_tt_arithmetic_mean():
    item = {"linkId": 1, "listSectionStatus": [
        {"speed": 40, "state": 1, "travelTime": 0}, {"speed": 60, "state": 1, "travelTime": 0},
    ]}
    out = parse_traffic_item(item)
    assert out["speed"] == 50 and out["travel_time"] is None


def test_parse_payload_and_empty():
    payload = {"linkStates": [
        {"linkId": 1, "speed": 80, "state": 1, "travelTime": 10},
        {"linkId": 2, "listSectionStatus": [{"speed": 50, "state": 2, "travelTime": 20}]},
    ]}
    out = list(parse_traffic(payload))
    assert [o["link_id"] for o in out] == [1, 2]
    assert list(parse_traffic({})) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/parsing/test_traffic.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/parsing/traffic.py`:
```python
"""Parse traffic/status into repository-ready rows, aggregating segmented links."""
from typing import Iterator, Optional


def _aggregate_sections(sections: list[dict]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    weighted = [(s["speed"], s.get("travelTime") or 0) for s in sections if s.get("speed") is not None]
    tt_sum = sum(tt for _, tt in weighted)
    if tt_sum > 0:
        speed: Optional[int] = round(sum(sp * tt for sp, tt in weighted) / tt_sum)
    elif weighted:
        speed = round(sum(sp for sp, _ in weighted) / len(weighted))
    else:
        speed = None

    states = [s.get("state") for s in sections if s.get("state") is not None]
    congested = [st for st in states if st != 5]  # 5 = unknown, lowest priority
    if congested:
        state: Optional[int] = max(congested)
    elif states:
        state = 5
    else:
        state = None

    travel_time = sum(s.get("travelTime") or 0 for s in sections) or None
    return speed, state, travel_time


def parse_traffic_item(item: dict) -> dict:
    """One linkStates element → {link_id, speed, state, travel_time} (sections aggregated)."""
    sections = item.get("listSectionStatus")
    if sections:
        speed, state, travel_time = _aggregate_sections(sections)
    else:
        speed = item.get("speed")
        state = item.get("state")
        travel_time = item.get("travelTime")
    return {"link_id": item["linkId"], "speed": speed, "state": state, "travel_time": travel_time}


def parse_traffic(payload: dict) -> Iterator[dict]:
    for item in payload.get("linkStates", []):
        yield parse_traffic_item(item)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/parsing/test_traffic.py -q`
Expected: PASS（6 passed）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/parsing/traffic.py tests/parsing/test_traffic.py
git commit -m "feat(parsing): traffic item/payload parsing with section aggregation"
```

---

## Task 5：路网流水线（mode-aware）

**Files:**
- Create: `amap_service/pipelines/__init__.py`（空）
- Create: `amap_service/pipelines/road_network.py`
- Test: `tests/pipelines/__init__.py`（空）、`tests/pipelines/test_road_network.py`

- [ ] **Step 1: 写失败测试**

`tests/pipelines/test_road_network.py`:
```python
import httpx
import pytest
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import road_link, road_link_coord
from amap_service.pipelines.road_network import run_road_network

PAYLOAD = {
    "linkCoordList": [
        {"linkId": 5130091959790075998,
         "coordList": [120.93746244907379, 31.06035053730011, 120.9343296289444, 31.05913281440735],
         "roadName": "G50沪渝高速", "length": 328, "formway": 1, "roadclass": 0},
        {"linkId": 5130091959790075999,
         "coordList": [120.9343296289444, 31.05913281440735, 120.9331226348877, 31.058687567710876],
         "roadName": "G50沪渝高速", "length": 125, "formway": 1, "roadclass": 0},
    ]
}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


def _client(tmp_path):
    def handler(request):
        assert request.url.path == "/g5_server/map/api/areaLinkPub"
        return httpx.Response(200, json=PAYLOAD)
    return HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("mode", ["memory", "stream"])
def test_run_road_network_both_modes(tmp_path, mode):
    e = _engine(tmp_path)
    client = _client(tmp_path)
    stats = run_road_network(e, client, "http://192.168.102.102:8080",
                             "/g5_server/map/api/areaLinkPub", parse_mode=mode)
    assert stats["inserted"] == 2 and stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(road_link)).scalar() == 2
        assert c.execute(select(func.count()).select_from(road_link_coord)).scalar() == 4
        # 64-bit link_id preserved; coords stored as float REAL (Decimal would fail to bind)
        assert c.execute(select(road_link.c.link_id).order_by(road_link.c.link_id)
                         ).scalars().first() == 5130091959790075998
        lng = c.execute(select(road_link_coord.c.longitude).order_by(road_link_coord.c.id)).scalars().first()
        assert isinstance(lng, float)
    client.close()


def test_invalid_mode_raises(tmp_path):
    e = _engine(tmp_path)
    client = _client(tmp_path)
    with pytest.raises(ValueError):
        run_road_network(e, client, "http://h", "/g5_server/map/api/areaLinkPub", parse_mode="bogus")
    client.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/pipelines/test_road_network.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/pipelines/__init__.py`: 空文件
`tests/pipelines/__init__.py`: 空文件

`amap_service/pipelines/road_network.py`:
```python
"""Road-network landing pipeline: fetch areaLinkPub → parse → upsert.

parse_mode:
  "memory" — one-shot get_json (retryable; whole response in RAM).
  "stream" — ijson stream_items (constant memory; suited to the 408k-link full dump).
"""
import logging

from sqlalchemy import Engine

from amap_service.clients.base import HttpClient
from amap_service.db.repositories import upsert_road_links
from amap_service.parsing.road_network import parse_road_link_item, parse_road_network

logger = logging.getLogger(__name__)


def run_road_network(
    engine: Engine, http_client: HttpClient, endpoint: str, path: str, parse_mode: str = "memory"
) -> dict:
    url = endpoint.rstrip("/") + path
    logger.info("road_network: fetching %s (mode=%s)", url, parse_mode)
    if parse_mode == "memory":
        rows = parse_road_network(http_client.get_json(url))
    elif parse_mode == "stream":
        rows = (parse_road_link_item(it) for it in http_client.stream_items(url, "linkCoordList.item"))
    else:
        raise ValueError(f"unknown parse_mode: {parse_mode}")
    stats = upsert_road_links(engine, rows)
    logger.info("road_network: done %s", stats)
    return stats
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/pipelines/test_road_network.py -q`
Expected: PASS（3 passed：memory + stream + invalid）。再跑全量。

- [ ] **Step 5: 提交**

```bash
git add amap_service/pipelines/__init__.py amap_service/pipelines/road_network.py tests/pipelines/__init__.py tests/pipelines/test_road_network.py
git commit -m "feat(pipelines): road network pipeline (memory|stream modes)"
```

---

## Task 6：路况流水线（mode-aware）

**Files:**
- Create: `amap_service/pipelines/traffic.py`
- Test: `tests/pipelines/test_traffic.py`

- [ ] **Step 1: 写失败测试**

`tests/pipelines/test_traffic.py`:
```python
import httpx
import pytest
from sqlalchemy import func, select
from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.clients.base import HttpClient
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.schema import traffic_status
from amap_service.pipelines.traffic import run_traffic

PAYLOAD = {
    "autolrDataVersion": "3.26.05.17",
    "linkStates": [
        {"linkId": 5130516143645130888, "speed": 89, "state": 1, "travelTime": 59},
        {"linkId": 5130516143645131894, "listSectionStatus": [
            {"offset": 3765, "reliability": 89, "speed": 88, "state": 1, "travelTime": 688},
            {"offset": 2165, "reliability": 89, "speed": 92, "state": 1, "travelTime": 307},
        ]},
    ],
}


def _engine(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    return e


@pytest.mark.parametrize("mode", ["memory", "stream"])
def test_run_traffic_both_modes(tmp_path, mode):
    e = _engine(tmp_path)
    def handler(request):
        assert request.url.path == "/g5_server/map/api/traffic/status"
        return httpx.Response(200, json=PAYLOAD)
    client = HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))
    stats = run_traffic(e, client, "http://192.168.102.102:8080/",
                        "/g5_server/map/api/traffic/status", parse_mode=mode)
    assert stats["inserted"] == 2 and stats["failed"] == 0
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 2
        agg = c.execute(
            select(traffic_status.c.speed, traffic_status.c.state, traffic_status.c.travel_time)
            .where(traffic_status.c.link_id == 5130516143645131894)
        ).one()
        assert tuple(agg) == (89, 1, 995)
    client.close()


def test_run_traffic_upsert_refresh(tmp_path):
    e = _engine(tmp_path)
    payloads = [
        {"linkStates": [{"linkId": 1, "speed": 80, "state": 1, "travelTime": 10}]},
        {"linkStates": [{"linkId": 1, "speed": 20, "state": 3, "travelTime": 40}]},
    ]
    def handler(request):
        return httpx.Response(200, json=payloads.pop(0))
    client = HttpClient(backoff_seconds=0, transport=httpx.MockTransport(handler))
    run_traffic(e, client, "http://h", "/g5_server/map/api/traffic/status")
    run_traffic(e, client, "http://h", "/g5_server/map/api/traffic/status")
    with e.connect() as c:
        assert c.execute(select(func.count()).select_from(traffic_status)).scalar() == 1
        assert tuple(c.execute(select(traffic_status.c.speed, traffic_status.c.state)).one()) == (20, 3)
    client.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/pipelines/test_traffic.py -q`
Expected: FAIL — import error

- [ ] **Step 3: 实现**

`amap_service/pipelines/traffic.py`:
```python
"""Realtime traffic landing pipeline: fetch traffic/status → parse → upsert (latest-only)."""
import logging

from sqlalchemy import Engine

from amap_service.clients.base import HttpClient
from amap_service.db.repositories import upsert_traffic_status
from amap_service.parsing.traffic import parse_traffic, parse_traffic_item

logger = logging.getLogger(__name__)


def run_traffic(
    engine: Engine, http_client: HttpClient, endpoint: str, path: str, parse_mode: str = "memory"
) -> dict:
    url = endpoint.rstrip("/") + path
    logger.info("traffic: fetching %s (mode=%s)", url, parse_mode)
    if parse_mode == "memory":
        rows = parse_traffic(http_client.get_json(url))
    elif parse_mode == "stream":
        rows = (parse_traffic_item(it) for it in http_client.stream_items(url, "linkStates.item"))
    else:
        raise ValueError(f"unknown parse_mode: {parse_mode}")
    stats = upsert_traffic_status(engine, rows)
    logger.info("traffic: done %s", stats)
    return stats
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/pipelines/test_traffic.py -q`
Expected: PASS（3 passed）。再跑全量 `python3 -m pytest -q`（只要全绿即可，不硬卡数字）。

- [ ] **Step 5: 提交**

```bash
git add amap_service/pipelines/traffic.py tests/pipelines/test_traffic.py
git commit -m "feat(pipelines): traffic pipeline (memory|stream modes)"
```

---

## 完成标准（Definition of Done）

- `python3 -m pytest -q` 全绿。
- 路网/路况两条流水线在 **memory 与 stream 两种模式**下均能拉取→解析→落库（参数化测试覆盖）。
- `parse_mode` 由配置（`JobConfig.parse_mode`）驱动，非法值被拒。
- 坐标在两种模式下都落为 `float`（stream 的 Decimal 已规范化）；64 位 `link_id` 全程 int 无损。
- 路况分段聚合到顶层；upsert 只存最新并刷新 updated_at。
- `get_json` 重试/放弃经测试覆盖；`stream_items` 不重试（已文档化，靠下一次 cron 周期重跑）。

## 已知简化（记录，不阻塞）

- `stream_items` 不做重试（流被消费后无法安全重放）；全量路网每日一次，失败下个周期重跑即可。若需更强可靠性，后续可加「失败后整次重连重试」。
- 解析对单条目异常不逐条捕获；脏数据隔离目前是 repositories 的「单批失败」粒度（Plan 1 评审 nit）。真实全量出现脏条目导致整批丢失时，再在 parsing/pipeline 加逐条 try/except 计入 skipped/failed。
- 鉴权：`auth.type=none`；HttpClient 支持注入 headers，待需要时由 daemon 装配（Plan 4）。
- 示例 `config/config.yaml` 建议把 `amap.jobs.road_network.parse_mode` 设为 `stream`（大响应省内存），`traffic_status` 用默认 `memory`（小、可重试）——此为运维选择，不在本计划代码内强制。

## 后续 Plan

- **Plan 4 — 调度 daemon + CLI + cache**：scheduler（APScheduler 按 cron 装配 run_road_network/run_traffic，传入各 job 的 parse_mode）、cli（run/run-once/initdb）、cache（NoOp/Redis）。
- **Plan 5 — 需求2 阶段一**：transit client（MD5 签名 + token 缓存）+ 链路 + 原始响应存档。
