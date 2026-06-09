"""API 共享依赖:从 app.state 取单例(engine/cache/static_cache/traffic_reader/config),与 API-Key 校验。"""
from fastapi import HTTPException, Request

from amap_service.config.schema import ApiAuthConfig


def check_api_key(auth: ApiAuthConfig, provided) -> None:
    """鉴权关 → 放行;鉴权开 → key 必须与配置一致,否则 401。"""
    if not auth.enabled:
        return
    if not provided or provided != auth.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing api key")


def get_config(request: Request):
    return request.app.state.config


def get_static_cache(request: Request):
    return request.app.state.static_cache


def get_traffic_reader(request: Request):
    return request.app.state.traffic_reader


def require_api_key(request: Request) -> None:
    """FastAPI 依赖:按配置 header 取 key 并校验。"""
    cfg = request.app.state.config.api.auth
    provided = request.headers.get(cfg.header)
    check_api_key(cfg, provided)
