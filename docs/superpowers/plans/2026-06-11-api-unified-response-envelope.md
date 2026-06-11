# /api/v1 统一响应信封 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 FastAPI `/api/v1` 下全部端点(成功与错误)统一返回 `{success, code, message, data, timestamp, requestid}` 信封。

**Architecture:** 方案 A —— 新增 `amap_service/api/envelope.py`(泛型 `Envelope[T]` 模型 + `success()` 帮助函数 + 时间戳工具);在 `create_app` 中注册一个 requestid 中间件和三个异常处理器(`HTTPException` / `RequestValidationError` / 兜底 `Exception`);6 个路由改为 `response_model=Envelope[...]` 并 `return success(view, request)`。`code` 镜像 HTTP 状态码,`requestid` 优先读 `X-Request-ID` 请求头否则生成 `req_<uuid>`。`demo/mock_api_server.py` 不动。

**Tech Stack:** Python, FastAPI/Starlette, pydantic v2(已用泛型),pytest + `fastapi.testclient.TestClient`。

参考 spec:`docs/superpowers/specs/2026-06-11-api-unified-response-envelope-design.md`

---

## File Structure

| 文件 | 职责 |
|------|------|
| `amap_service/api/envelope.py` **(新建)** | `Envelope[T]` 模型、`now_iso_millis()`、`success()`、`error_response()`(构造失败信封 `JSONResponse`) |
| `amap_service/api/app.py` **(修改)** | 注册 `RequestIdMiddleware` + 三个异常处理器 |
| `amap_service/api/routes.py` **(修改)** | 6 个端点改 `response_model=Envelope[...]`,`return success(...)` |
| `amap_service/api/schemas.py` **(修改)** | 新增 `HealthStatus` 模型 |
| `tests/api/test_envelope.py` **(新建)** | envelope 模块单元测试 + 信封行为(成功/错误/requestid/timestamp)集成测试 |
| `tests/api/test_app.py` **(修改)** | 现有断言改为从 `data` 解包 |
| `docs/命令说明.md` **(修改)** | 标注 `/api/v1` 响应已统一为信封格式 |

---

### Task 1: Envelope 模型与帮助函数

**Files:**
- Create: `amap_service/api/envelope.py`
- Test: `tests/api/test_envelope.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/test_envelope.py`:

```python
import re

from amap_service.api.envelope import Envelope, now_iso_millis, success

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class _StubState:
    request_id = "req_test123"


class _StubRequest:
    state = _StubState()


def test_now_iso_millis_format():
    assert TS_RE.match(now_iso_millis())


def test_success_builds_envelope():
    env = success({"x": 1}, _StubRequest())
    assert isinstance(env, Envelope)
    assert env.success is True
    assert env.code == 200
    assert env.message == "OK"
    assert env.data == {"x": 1}
    assert env.requestid == "req_test123"
    assert TS_RE.match(env.timestamp)


def test_success_custom_message_and_code():
    env = success(None, _StubRequest(), message="created", code=201)
    assert env.code == 201 and env.message == "created" and env.success is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/api/test_envelope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'amap_service.api.envelope'`

- [ ] **Step 3: 实现 envelope.py**

创建 `amap_service/api/envelope.py`:

```python
"""统一响应信封。code 镜像 HTTP 状态码;requestid 由中间件写入 request.state。"""
from datetime import datetime, timezone
from typing import Generic, Optional, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    success: bool
    code: int
    message: str
    data: Optional[T] = None
    timestamp: str
    requestid: str


def now_iso_millis() -> str:
    """UTC ISO8601,毫秒精度带 Z,如 2024-01-15T10:30:00.000Z。"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def success(data, request, message: str = "OK", code: int = 200) -> Envelope:
    """构造成功信封。data 在此不做类型校验;路由的 response_model=Envelope[T] 负责收敛。"""
    return Envelope(
        success=True, code=code, message=message, data=data,
        timestamp=now_iso_millis(), requestid=request.state.request_id,
    )


def error_response(code: int, message: str, request_id: str) -> JSONResponse:
    """构造失败信封 JSONResponse(HTTP 状态码与信封 code 一致,data=null)。"""
    return JSONResponse(
        status_code=code,
        content={
            "success": False, "code": code, "message": message, "data": None,
            "timestamp": now_iso_millis(), "requestid": request_id,
        },
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/api/test_envelope.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add amap_service/api/envelope.py tests/api/test_envelope.py
git commit -m "feat(api): 统一响应信封模型与 success/error 帮助函数"
```

---

### Task 2: RequestId 中间件

**Files:**
- Modify: `amap_service/api/app.py`
- Test: `tests/api/test_envelope.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/api/test_envelope.py` 顶部追加复用 `tests/api/test_app.py` 的客户端构造(直接 import 其 helper):

```python
from tests.api.test_app import _client  # 复用现有 TestClient 工厂


def test_request_id_generated_and_echoed(tmp_path):
    r = _client(tmp_path).get("/api/v1/health")
    rid = r.headers.get("X-Request-ID")
    assert rid is not None and rid.startswith("req_")


def test_request_id_passthrough(tmp_path):
    r = _client(tmp_path).get("/api/v1/health", headers={"X-Request-ID": "req_caller99"})
    assert r.headers["X-Request-ID"] == "req_caller99"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/api/test_envelope.py -k request_id -v`
Expected: FAIL — `assert None is not None`(响应头无 `X-Request-ID`)

- [ ] **Step 3: 实现中间件并注册**

修改 `amap_service/api/app.py`。在文件顶部 import 区追加:

```python
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
```

在 `create_app` 定义之前新增中间件类:

```python
class RequestIdMiddleware(BaseHTTPMiddleware):
    """读 X-Request-ID 否则生成 req_<uuid>,存 request.state.request_id,并回写响应头。"""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
```

在 `create_app` 内、`return app` 之前(`include_router` 之后即可)注册:

```python
    app.add_middleware(RequestIdMiddleware)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/api/test_envelope.py -k request_id -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add amap_service/api/app.py tests/api/test_envelope.py
git commit -m "feat(api): RequestId 中间件(透传/生成 X-Request-ID)"
```

---

### Task 3: 异常处理器(错误信封全覆盖)

**Files:**
- Modify: `amap_service/api/app.py`
- Test: `tests/api/test_envelope.py`(追加)

- [ ] **Step 1: 写失败测试**

在 `tests/api/test_envelope.py` 追加(注意 500 用例需关闭 TestClient 的服务端异常抛出):

```python
from fastapi.testclient import TestClient

from amap_service.api.app import create_app
from tests.api.test_app import _base_config
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db


def test_error_404_enveloped(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/999/segments")
    body = r.json()
    assert r.status_code == 404
    assert body["success"] is False and body["code"] == 404
    assert body["data"] is None
    assert "999" in body["message"]
    assert body["requestid"].startswith("req_")
    assert TS_RE.match(body["timestamp"])


def test_error_401_enveloped(tmp_path):
    r = _client(tmp_path, auth_enabled=True).get("/api/v1/lines")
    body = r.json()
    assert r.status_code == 401 and body["code"] == 401 and body["success"] is False


def test_error_422_enveloped(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments?direction=5")
    body = r.json()
    assert r.status_code == 422 and body["code"] == 422
    assert body["data"] is None and "direction" in body["message"]


def test_error_500_enveloped(tmp_path):
    db_path = str(tmp_path / "t.db")
    cfg = _base_config(db_path)
    eng = make_engine(cfg.database)
    init_db(eng)
    app = create_app(cfg, engine=eng)

    @app.get("/api/v1/_boom")
    def _boom():
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/_boom")
    body = r.json()
    assert r.status_code == 500 and body["code"] == 500
    assert body["success"] is False and body["data"] is None
    assert body["message"] == "Internal Server Error"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/api/test_envelope.py -k error -v`
Expected: FAIL — 404/401 当前返回 `{"detail": ...}`,无 `success`/`code` 键(`KeyError` 或断言失败)

- [ ] **Step 3: 实现异常处理器**

修改 `amap_service/api/app.py`。import 区追加:

```python
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from amap_service.api.envelope import error_response
```

在 `create_app` 内,`include_router` 之后、`add_middleware` 附近,注册三个处理器:

```python
    @app.exception_handler(HTTPException)
    async def _http_exc(request, exc):
        return error_response(exc.status_code, str(exc.detail), request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request, exc):
        msg = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return error_response(422, msg, request.state.request_id)

    @app.exception_handler(Exception)
    async def _unhandled_exc(request, exc):
        return error_response(500, "Internal Server Error", request.state.request_id)
```

> 说明:`RequestIdMiddleware` 在路由/异常处理之前已设置 `request.state.request_id`,故错误路径可用。`Exception` 处理器不泄露堆栈,仅返回固定 message。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/api/test_envelope.py -k error -v`
Expected: PASS(4 passed)

- [ ] **Step 5: 提交**

```bash
git add amap_service/api/app.py tests/api/test_envelope.py
git commit -m "feat(api): HTTPException/校验/兜底异常统一返回错误信封"
```

---

### Task 4: 成功响应包入信封 + HealthStatus

**Files:**
- Modify: `amap_service/api/schemas.py`
- Modify: `amap_service/api/routes.py`
- Modify: `tests/api/test_app.py`(现有断言改为从 `data` 解包)
- Test: `tests/api/test_envelope.py`(追加成功用例)

- [ ] **Step 1: 写失败测试**

在 `tests/api/test_envelope.py` 追加:

```python
def test_success_lines_enveloped(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines")
    body = r.json()
    assert r.status_code == 200
    assert body["success"] is True and body["code"] == 200 and body["message"] == "OK"
    assert body["data"][0]["line_name"] == "47"
    assert body["requestid"].startswith("req_")
    assert TS_RE.match(body["timestamp"])


def test_success_segments_preserves_link_id_string(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["link_id"] == "5130091959790075998"  # 字符串,防 JS 损精度


def test_success_health_enveloped(tmp_path):
    body = _client(tmp_path).get("/api/v1/health").json()
    assert body["success"] is True and body["data"]["status"] == "ok"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/api/test_envelope.py -k "success_lines or success_segments or success_health" -v`
Expected: FAIL — 当前 `/lines` 返回裸 list,`body["success"]` 触发 `TypeError`/`KeyError`

- [ ] **Step 3: 新增 HealthStatus 模型**

在 `amap_service/api/schemas.py` 末尾追加:

```python
class HealthStatus(BaseModel):
    status: str
```

- [ ] **Step 4: 路由改为返回信封**

把 `amap_service/api/routes.py` 整体替换为:

```python
"""需求3/4/5 路由。需求4/5 响应体与对应 MQTT 主题 payload 同构(同一视图层产出)。
全部端点统一返回 Envelope 信封(success/code/message/data/timestamp/requestid)。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from amap_service.api import schemas
from amap_service.api.deps import require_api_key
from amap_service.api.envelope import Envelope, success
from amap_service.views.line_views import (
    build_segment_view, build_traffic_view, build_section_view, build_station_section_view,
)

router = APIRouter(prefix="/api/v1")

health_router = APIRouter(prefix="/api/v1")


@health_router.get("/health", response_model=Envelope[schemas.HealthStatus])
def health(request: Request):
    return success({"status": "ok"}, request)


@router.get("/lines", response_model=Envelope[list[schemas.LineSummary]],
            dependencies=[Depends(require_api_key)])
def list_lines(request: Request):
    return success(request.app.state.static_cache.lines(), request)


@router.get("/lines/{line_name}/segments", response_model=Envelope[schemas.SegmentView],
            dependencies=[Depends(require_api_key)])
def line_segments(request: Request, line_name: str,
                  direction: Optional[int] = Query(default=None, ge=0, le=1)):
    view = build_segment_view(request.app.state.static_cache, line_name, direction)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return success(view, request)


@router.get("/lines/{line_name}/traffic", response_model=Envelope[schemas.TrafficView],
            dependencies=[Depends(require_api_key)])
def line_traffic(request: Request, line_name: str,
                 direction: Optional[int] = Query(default=None, ge=0, le=1),
                 geometry: bool = Query(default=False)):
    view = build_traffic_view(request.app.state.static_cache,
                              request.app.state.traffic_reader, line_name, direction, geometry)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return success(view, request)


@router.get("/lines/{line_name}/sections", response_model=Envelope[schemas.SectionView],
            dependencies=[Depends(require_api_key)])
def line_sections(request: Request, line_name: str,
                  direction: Optional[int] = Query(default=None, ge=0, le=1),
                  geometry: bool = Query(default=False)):
    view = build_section_view(request.app.state.static_cache,
                              request.app.state.traffic_reader, line_name, direction, geometry)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return success(view, request)


@router.get("/lines/{line_name}/sections/{to_level_id}",
            response_model=Envelope[schemas.StationSectionView],
            dependencies=[Depends(require_api_key)])
def line_single_section(request: Request, line_name: str, to_level_id: int,
                        direction: int = Query(..., ge=0, le=1),
                        geometry: bool = Query(default=False)):
    view = build_station_section_view(request.app.state.static_cache,
                                      request.app.state.traffic_reader,
                                      line_name, direction, to_level_id, geometry)
    if view is None:
        raise HTTPException(status_code=404,
                            detail=f"section not found: {line_name}/{direction}/{to_level_id}")
    return success(view, request)
```

- [ ] **Step 5: 更新现有 test_app.py 断言(从 data 解包)**

把 `tests/api/test_app.py` 中第 60–119 行的测试函数替换为(其余文件不变):

```python
def test_health(tmp_path):
    r = _client(tmp_path).get("/api/v1/health")
    assert r.status_code == 200 and r.json()["data"]["status"] == "ok"


def test_lines(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines")
    assert r.status_code == 200
    assert r.json()["data"][0]["line_name"] == "47"


def test_segments_req3(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["link_id"] == "5130091959790075998"
    assert seg["line_track"] == "121.1,31.1;121.2,31.2"


def test_traffic_req4_lean_default(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/traffic")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["state"] == 2 and seg["speed"] == 18
    assert seg.get("line_track") is None


def test_traffic_req4_geometry_true(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/traffic?geometry=true")
    seg = r.json()["data"]["directions"][0]["segments"][0]
    assert seg["line_track"] == "121.1,31.1;121.2,31.2"


def test_sections_req5(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/sections")
    sec = r.json()["data"]["directions"][0]["sections"][0]
    assert sec["from_level_id"] == 1 and sec["to_level_id"] == 2
    assert sec["links"][0]["pct"] == 100 and sec["links"][0]["state"] == 2


def test_single_section_req5(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/sections/2?direction=0")
    assert r.json()["data"]["to_level_id"] == 2
    assert r.json()["data"]["links"][0]["state"] == 2


def test_unknown_line_404(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/999/segments")
    assert r.status_code == 404


def test_bad_direction_422(tmp_path):
    r = _client(tmp_path).get("/api/v1/lines/47/segments?direction=5")
    assert r.status_code == 422


def test_auth_required_when_enabled(tmp_path):
    c = _client(tmp_path, auth_enabled=True)
    assert c.get("/api/v1/lines").status_code == 401
    assert c.get("/api/v1/lines", headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/api/v1/health").status_code == 200
```

- [ ] **Step 6: 运行全部 api 测试确认通过**

Run: `python -m pytest tests/api/ -v`
Expected: PASS(test_envelope.py 全部 + test_app.py 全部 + test_deps.py 全部)

- [ ] **Step 7: 提交**

```bash
git add amap_service/api/schemas.py amap_service/api/routes.py tests/api/test_app.py tests/api/test_envelope.py
git commit -m "feat(api): 全部 /api/v1 成功响应包入统一信封 + HealthStatus"
```

---

### Task 5: 更新接口文档

**Files:**
- Modify: `docs/命令说明.md`

- [ ] **Step 1: 定位 API 端点章节**

Run: `grep -n "API 端点" docs/命令说明.md`
Expected: 命中「### API 端点(前缀 `/api/v1`)」一行(约 191 行)。

- [ ] **Step 2: 在该章节标题下追加统一响应说明**

在「### API 端点(前缀 `/api/v1`)」标题行的下一行插入:

````markdown
> **统一响应格式**:`/api/v1` 下全部端点(含 `/health` 与所有错误)均返回信封:
> ```json
> {"success": true, "code": 200, "message": "OK",
>  "data": { ... }, "timestamp": "2024-01-15T10:30:00.000Z", "requestid": "req_abc123"}
> ```
> `code` 镜像 HTTP 状态码;失败时 `success=false`、`data=null`、`message` 为错误说明。
> 请求可携带 `X-Request-ID` 头透传链路 ID,否则服务端生成 `req_<uuid>` 并在响应头回写。
````

- [ ] **Step 3: 提交**

```bash
git add docs/命令说明.md
git commit -m "docs: 标注 /api/v1 统一信封响应格式"
```

---

## 验收(全部任务完成后)

- [ ] Run: `python -m pytest tests/ -q` —— 全仓库测试通过(确认信封改动未破坏其他模块)。
- [ ] Run: 启动服务后 `curl -s http://127.0.0.1:8080/api/v1/health | python -m json.tool` —— 看到完整信封。

## Self-Review 记录

- **Spec 覆盖**:全覆盖(成功+404/401/422/500)→ Task 3+4;`code` 镜像 HTTP → `error_response`/`success` 默认;requestid 头透传/生成 → Task 2;timestamp 毫秒带 Z → `now_iso_millis` + 各测试正则;OpenAPI 类型化 → Task 4 `response_model=Envelope[...]`;`/health` 包入 → Task 4;mock server 不动 → 文件结构未列入。✅
- **占位符**:无 TBD/TODO;每个代码步骤均给出完整代码。✅
- **类型一致**:`Envelope`、`success(data, request, message, code)`、`error_response(code, message, request_id)`、`now_iso_millis()`、`RequestIdMiddleware`、`HealthStatus` 在定义与引用处签名一致。✅
- **现有测试**:`tests/api/test_app.py` 旧断言会因包信封而失败 → Task 4 Step 5 显式更新。✅
