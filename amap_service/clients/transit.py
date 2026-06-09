"""Transit (bus-line) upstream client: signed token, line list, line entity.

Capture-first: methods do NOT raise on non-2xx — the response body is returned so the
stage-1 pipeline can archive even error responses (useful while the schema is unknown).
"""
import datetime
import hashlib
import logging
import time
from typing import Optional

import httpx

from amap_service.parsing.transit import extract_token

logger = logging.getLogger(__name__)

_TOKEN_CACHE_KEY = "transit:token"
_LINE_LIST_CACHE_KEY = "transit:line_list"


def seconds_until_next_local_hour(now_ms: int, hour: int, tz_offset_hours: int = 8) -> int:
    """从 now_ms 到下一个 hour:00（指定时区，默认 UTC+8）的秒数（≥1）。

    线路缓存据此设 TTL：当日内一直命中，跨过该整点（默认 01:00）后自动失效，
    让每天刷新的线路数据被重新拉取。
    """
    tz = datetime.timezone(datetime.timedelta(hours=tz_offset_hours))
    now = datetime.datetime.fromtimestamp(now_ms / 1000, tz)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def build_signature(username: str, password: str, ts: int) -> str:
    """MD5('appsecret{pwd}appkey{user}timestamp{ts}appsecret{pwd}'), lowercase hex (note.md)."""
    unsign = f"appsecret{password}appkey{username}timestamp{ts}appsecret{password}"
    return hashlib.md5(unsign.encode()).hexdigest()


def build_token_body(username: str, password: str, ts: int) -> str:
    sign = build_signature(username, password, ts)
    return f"appkey={username}&sign={sign}&timestamp={ts}"


class TransitClient:
    def __init__(self, config_transit, *, transport=None, timeout: float = 30.0,
                 cache=None, token_cache_enabled: bool = False, now_ms=None,
                 line_cache_enabled: bool = False, line_cache_expire_hour: int = 1):
        self._t = config_transit
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._cache = cache
        self._token_cache_enabled = token_cache_enabled
        self._line_cache_enabled = line_cache_enabled
        self._line_cache_expire_hour = line_cache_expire_hour
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._mem_token: Optional[str] = None

    def _cache_on(self) -> bool:
        return self._cache is not None and getattr(self._cache, "enabled", False)

    def _redis_enabled(self) -> bool:
        return self._token_cache_enabled and self._cache_on()

    def _line_cache_on(self) -> bool:
        return self._line_cache_enabled and self._cache_on()

    def _line_ttl(self) -> int:
        return seconds_until_next_local_hour(self._now_ms(), self._line_cache_expire_hour)

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
        # token is passed as a standard Bearer credential on the line-list/entity calls
        return {"Authorization": f"Bearer {token}"} if token else {}

    def get_line_list(self, token: Optional[str]) -> str:
        if self._line_cache_on():
            cached = self._cache.get(_LINE_LIST_CACHE_KEY)
            if cached is not None:
                return cached
        loginname = self._t.loginname or self._t.username  # dedicated loginname, fallback to appkey
        resp = self._client.get(
            self._t.line_list_url, params={"loginname": loginname},
            headers=self._auth_headers(token),
        )
        if resp.status_code >= 300:
            logger.warning("transit get_line_list: HTTP %s", resp.status_code)
        elif self._line_cache_on():
            self._cache.set(_LINE_LIST_CACHE_KEY, resp.text, ttl=self._line_ttl())
        return resp.text

    def get_line_entity(self, token: Optional[str], line_name: str) -> str:
        key = f"transit:line_entity:{line_name}"
        if self._line_cache_on():
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        resp = self._client.get(
            self._t.line_entity_url, params={"lineName": line_name},
            headers=self._auth_headers(token),
        )
        if resp.status_code >= 300:
            logger.warning("transit get_line_entity(%s): HTTP %s", line_name, resp.status_code)
        elif self._line_cache_on():
            self._cache.set(key, resp.text, ttl=self._line_ttl())
        return resp.text

    def close(self) -> None:
        self._client.close()
