"""FastAPI 应用工厂。单例(engine/cache/static_cache/traffic_reader/config)挂 app.state。"""
from fastapi import FastAPI

from amap_service.api.routes import router, health_router
from amap_service.cache.client import make_cache
from amap_service.db.engine import make_engine
from amap_service.sdk.traffic_query import TrafficReader
from amap_service.views.static_cache import StaticLineCache


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
    return app
