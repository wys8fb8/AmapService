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
    on_complete=None,
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
    if not use_cache and on_complete is None:
        stats = upsert_traffic_status(engine, rows)
        logger.info("traffic: done %s", stats)
        return stats

    all_rows = list(rows)  # 全量(可能多次遍历: 增量比对 / 全量镜像 / on_complete)

    to_write = all_rows
    new_sigs = {}
    if use_cache and incremental:
        sig_keys = [f"traffic:sig:{r['link_id']}" for r in all_rows]
        old_sigs = cache.mget(sig_keys)
        changed = []
        for row, old in zip(all_rows, old_sigs):
            sig = _signature(row)
            if old != sig:
                changed.append(row)
                new_sigs[f"traffic:sig:{row['link_id']}"] = sig
        to_write = changed

    stats = upsert_traffic_status(engine, to_write)

    # 仅在 DB 写入成功后推进签名/快照，避免失败批次被误判为"未变"而漏写。
    if use_cache and not stats["failed"]:
        if incremental and new_sigs:
            cache.mset(new_sigs, ttl=traffic_ttl_seconds)
        if snapshot:
            # 全量镜像: 写所有 link 的最新值(非变更子集)，让 Redis 成为完整最新快照。
            cache.mset({f"traffic:latest:{r['link_id']}": json.dumps(r) for r in all_rows},
                       ttl=traffic_ttl_seconds)

    if on_complete is not None:
        try:
            on_complete(all_rows)
        except Exception:  # noqa: BLE001 — 发布失败绝不拖垮数据落地
            logger.exception("traffic: on_complete callback failed")

    logger.info("traffic: done %s (cached: snapshot=%s incremental=%s)", stats, snapshot, incremental)
    return stats
