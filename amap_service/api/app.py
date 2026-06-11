"""FastAPI 应用工厂。单例(engine/cache/static_cache/traffic_reader/config)挂 app.state。"""
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from amap_service.api.envelope import error_response
from amap_service.api.routes import router, health_router
from amap_service.cache.client import make_cache
from amap_service.db.engine import make_engine
from amap_service.sdk.traffic_query import TrafficReader
from amap_service.views.static_cache import StaticLineCache


class RequestIdMiddleware(BaseHTTPMiddleware):
    """读 X-Request-ID 否则生成 req_<uuid>,存 request.state.request_id,并回写响应头。"""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def create_app(config, engine=None) -> FastAPI:
    app = FastAPI(title="Amap 服务层 API", version="1.0")
    engine = engine if engine is not None else make_engine(config.database)
    cache = make_cache(config.redis)
    app.state.config = config
    app.state.engine = engine
    app.state.cache = cache
    app.state.static_cache = StaticLineCache(engine, ttl_seconds=config.api.static_cache_ttl_seconds)
    app.state.traffic_reader = TrafficReader(engine, cache=cache)
    app.include_router(health_router)
    app.include_router(router)
    app.add_middleware(RequestIdMiddleware)

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
        rid = getattr(request.state, "request_id", "req_unknown")
        return error_response(500, "Internal Server Error", rid)

    return app
