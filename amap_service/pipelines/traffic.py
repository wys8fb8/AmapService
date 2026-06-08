"""Realtime traffic landing pipeline: fetch traffic/status → parse → upsert (latest-only).

Optional Redis cache:
  incremental — skip links whose (speed,state,travel_time) signature is unchanged.
  snapshot    — write each landed row's latest values to the cache.
When no enabled cache is supplied, the original streaming/generator path is used unchanged.
"""
import json
import logging

from sqlalchemy import Engine

from amap_service.clients.base import HttpClient
from amap_service.db.repositories import upsert_traffic_status
from amap_service.parsing.traffic import parse_traffic, parse_traffic_item

logger = logging.getLogger(__name__)


def _signature(row: dict) -> str:
    return f"{row['speed']}:{row['state']}:{row['travel_time']}"


def run_traffic(
    engine: Engine,
    http_client: HttpClient,
    endpoint: str,
    path: str,
    parse_mode: str = "memory",
    cache=None,
    snapshot: bool = False,
    incremental: bool = False,
) -> dict:
    url = endpoint.rstrip("/") + path
    logger.info("traffic: fetching %s (mode=%s)", url, parse_mode)
    if parse_mode == "memory":
        rows = parse_traffic(http_client.get_json(url))
    elif parse_mode == "stream":
        rows = (parse_traffic_item(it) for it in http_client.stream_items(url, "linkStates.item"))
    else:
        raise ValueError(f"unknown parse_mode: {parse_mode}")

    use_cache = cache is not None and getattr(cache, "enabled", False) and (snapshot or incremental)
    if not use_cache:
        stats = upsert_traffic_status(engine, rows)
        logger.info("traffic: done %s", stats)
        return stats

    rows = list(rows)  # cache path needs multiple passes over the rows
    if incremental:
        changed = []
        for row in rows:
            key = f"traffic:sig:{row['link_id']}"
            sig = _signature(row)
            if cache.get(key) != sig:
                cache.set(key, sig)
                changed.append(row)
        rows = changed

    stats = upsert_traffic_status(engine, rows)

    if snapshot:
        for row in rows:
            cache.set(f"traffic:latest:{row['link_id']}", json.dumps(row))

    logger.info("traffic: done %s (cached: snapshot=%s incremental=%s)", stats, snapshot, incremental)
    return stats
