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
    """构造失败信封 JSONResponse(HTTP 状态码与信封 code 一致,data=null)。
    同时回写 X-Request-ID 响应头(兜底:500 走 ServerErrorMiddleware 时中间件不再覆盖头)。"""
    resp = JSONResponse(
        status_code=code,
        content={
            "success": False, "code": code, "message": message, "data": None,
            "timestamp": now_iso_millis(), "requestid": request_id,
        },
    )
    resp.headers["X-Request-ID"] = request_id
    return resp
