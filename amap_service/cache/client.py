"""Optional Redis cache. NoOpCache when disabled — callers need no `if enabled` branches."""
from typing import Optional

from amap_service.config.schema import RedisConfig


class NoOpCache:
    enabled = False

    def get(self, key: str) -> Optional[str]:
        return None

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        pass

    def mget(self, keys: list) -> list:
        return [None] * len(keys)

    def mset(self, mapping: dict, ttl: Optional[int] = None) -> None:
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

    def mget(self, keys: list) -> list:
        if not keys:
            return []
        vals = self._r.mget(keys)
        return [v.decode() if isinstance(v, (bytes, bytearray)) else v for v in vals]

    def mset(self, mapping: dict, ttl: Optional[int] = None) -> None:
        if not mapping:
            return
        if ttl:
            pipe = self._r.pipeline()
            for k, v in mapping.items():
                pipe.setex(k, ttl, v)
            pipe.execute()
        else:
            self._r.mset(mapping)


def _redis_client_from_config(cfg: RedisConfig):
    import redis  # imported lazily so a disabled cache needs no redis server/lib at runtime

    return redis.Redis(host=cfg.host, port=cfg.port, db=cfg.db, password=cfg.password)


def make_cache(cfg: RedisConfig):
    """Return RedisCache when enabled, else NoOpCache."""
    if not cfg.enabled:
        return NoOpCache()
    return RedisCache(_redis_client_from_config(cfg))
