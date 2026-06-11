# 设计:`/api/v1` 统一响应信封

日期:2026-06-11
状态:已确认,待生成实现计划

## 背景与目标

`amap_service/api/`(FastAPI)当前每个端点直接返回业务数据(`return view`、`return {"status": "ok"}`),错误则走 FastAPI 默认的 `{"detail": "..."}`。客户端因此要处理两种不同的响应形态。

目标:让 `### API 端点(前缀 /api/v1)` 下的**全部端点**(成功与错误)统一返回如下信封:

```json
{
  "success": true,
  "code": 200,
  "message": "对应状态码的说明",
  "data": { ... },
  "timestamp": "2024-01-15T10:30:00.000Z",
  "requestid": "req_abc123"
}
```

## 范围

- **在范围内**:FastAPI 应用 `amap_service/api/` 下 `/api/v1` 前缀的全部路由,包括 `/health`、`/lines`、`/lines/{line_name}/segments`、`/lines/{line_name}/traffic`、`/lines/{line_name}/sections`、`/lines/{line_name}/sections/{to_level_id}`。
- **不在范围内**:`demo/mock_api_server.py`(模拟高德上游的假服务器,必须保持高德原始格式不变);其他非 HTTP 模块。

## 已确认决策

| 决策点 | 选择 |
|--------|------|
| 覆盖范围 | 成功 + 错误(404/401/422/500)全覆盖 |
| `code` 含义 | 镜像 HTTP 状态码 |
| `requestid` 来源 | 优先读请求头 `X-Request-ID`,否则生成 `req_<uuid>` |
| 实现方式 | 类型化信封 `Envelope[T]` + 异常处理器(方案 A) |

## 架构与组件

新增 1 个文件,改动 3 个文件,范围严格限定在 `amap_service/api/`:

| 文件 | 改动 |
|------|------|
| `amap_service/api/envelope.py` **(新)** | 泛型 `Envelope[T]` 模型 + `success()` 帮助函数 + 时间戳/requestid 工具 |
| `amap_service/api/routes.py` | 6 个端点改 `response_model=Envelope[...]`,`return success(view, request)` |
| `amap_service/api/app.py` | 注册 3 个异常处理器 + requestid 中间件 |
| `amap_service/api/schemas.py` | 新增极小的 `HealthStatus` 模型(给 `/health` 用) |

## 信封结构与字段规则

```python
from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class Envelope(BaseModel, Generic[T]):
    success: bool
    code: int                    # 镜像 HTTP 状态码
    message: str
    data: Optional[T] = None
    timestamp: str               # "2024-01-15T10:30:00.000Z" UTC ISO8601 毫秒
    requestid: str
```

字段规则:

- **success**:`code` 在 200–399 → `true`,否则 `false`。
- **code**:镜像 HTTP 状态码(200 / 401 / 404 / 422 / 500)。
- **message**:成功默认 `"OK"`(端点可在调用 `success()` 时覆盖);失败取异常 detail / 错误摘要。
- **timestamp**:`datetime.now(timezone.utc)` 格式化为毫秒精度带 `Z`(`%Y-%m-%dT%H:%M:%S.%f` 截断到毫秒 + `Z`)。
- **data**:成功为业务数据;失败一律 `null`。
- **requestid**:见下文「requestid 与响应头」。

### `success()` 帮助函数

```python
def success(data, request, message: str = "OK", code: int = 200):
    return Envelope(
        success=True, code=code, message=message, data=data,
        timestamp=now_iso_millis(), requestid=request.state.request_id,
    )
```

`success()` 返回未参数化的 `Envelope`(`data` 视为 `Any`,不在此处强制校验);路由上的 `response_model=Envelope[SegmentView]` 负责对整体信封做校验与 OpenAPI 文档化,并把 `data` 内的 dict 收敛为对应类型(沿用现有 `link_id` 字符串化行为)。

## 错误处理(全覆盖的关键)

在 `create_app` 中注册 3 个异常处理器,保证错误也走同一信封:

| 处理器 | code | message | data |
|--------|------|---------|------|
| `HTTPException`(404/401 等) | `exc.status_code` | `exc.detail`(如 `"line not found: 47"`) | `null` |
| `RequestValidationError` | `422` | 拼接的字段校验摘要(如 `"direction: input should be less than or equal to 1"`) | `null` |
| 兜底 `Exception` | `500` | `"Internal Server Error"`(不泄露堆栈) | `null` |

三者都填充 `timestamp` 和 `requestid`,以 `JSONResponse(status_code=code, content=...)` 返回,HTTP 状态码与信封 `code` 保持一致。

## requestid 与响应头

一个轻量 `BaseHTTPMiddleware`:

- 进入时读请求头 `X-Request-ID`,有则透传,无则生成 `req_<uuid4.hex>`,存入 `request.state.request_id`。
- 出去时把该值写回响应头 `X-Request-ID`(便于跨服务链路追踪)。
- `success()` 与全部异常处理器都从 `request.state.request_id` 读取。

> 注意:异常处理器在中间件已设置 `request.state.request_id` 之后运行,因此 requestid 在错误路径同样可用。

## OpenAPI / 文档

- 路由使用 `response_model=Envelope[SegmentView]` / `Envelope[list[LineSummary]]` / `Envelope[HealthStatus]` 等,Swagger 会完整且类型准确地展示每个端点的信封结构。
- 错误信封可通过路由的 `responses={404: ..., 422: ...}` 声明补充文档(可选,提升体验,不影响行为)。
- 完成后同步更新 `docs/命令说明.md` 中「API 端点(前缀 /api/v1)」一节,标注响应已统一为信封格式。

## 测试(TDD)

新增 `tests/api/test_envelope.py`,用 FastAPI `TestClient`:

- **成功路径**:`/lines`、`/lines/{n}/segments` 等返回 `success=true` / `code=200`;`data` 内 `link_id` 仍是字符串(不破坏防 JS 损精度)。
- **404**:不存在线路 → `success=false` / `code=404` / `data=null` / message 含线路名。
- **401**:鉴权开 + 无 key → `code=401`。
- **422**:非法 `direction=2` → `code=422`,message 含字段名。
- **requestid**:传入 `X-Request-ID` 被透传到 `data` 同级的 `requestid` 与响应头;不传则生成 `req_` 前缀值且响应头回写。
- **timestamp**:正则校验 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`。
- **`/health`**:同样被包进信封(`data.status == "ok"`)。

## 非目标 / YAGNI

- 不引入独立业务错误码体系(`code` 直接镜像 HTTP 状态)。
- 不对 `data` 之外做分页/元数据扩展。
- 不改动 MQTT publish 路径与 `mock_api_server.py`。
