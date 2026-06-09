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
    traffic_ttl_seconds: int = 600,
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

    rows = list(rows)  # cache path needs multiple passes
    new_sigs = {}
    if incremental:
        sig_keys = [f"traffic:sig:{r['link_id']}" for r in rows]
        old_sigs = cache.mget(sig_keys)
        changed = []
        for row, old in zip(rows, old_sigs):
            sig = _signature(row)
            if old != sig:
                changed.append(row)
                new_sigs[f"traffic:sig:{row['link_id']}"] = sig
        rows = changed

    stats = upsert_traffic_status(engine, rows)

    # 仅在 DB 全部写入成功后才推进签名/快照，避免失败批次被误判为"未变"而漏写。
    if not stats["failed"]:
        if incremental and new_sigs:
            cache.mset(new_sigs, ttl=traffic_ttl_seconds)
        if snapshot and rows:
            cache.mset({f"traffic:latest:{row['link_id']}": json.dumps(row) for row in rows},
                       ttl=traffic_ttl_seconds)

    logger.info("traffic: done %s (cached: snapshot=%s incremental=%s)", stats, snapshot, incremental)
    return stats
