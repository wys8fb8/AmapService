"""Road-network landing pipeline: fetch areaLinkPub → parse → upsert.

parse_mode:
  "memory" — one-shot get_json (retryable; whole response in RAM).
  "stream" — ijson stream_items (constant memory; suited to the 408k-link full dump).
"""
import logging

from sqlalchemy import Engine

from amap_service.clients.base import HttpClient
from amap_service.db.repositories import upsert_road_links
from amap_service.parsing.road_network import parse_road_link_item, parse_road_network

logger = logging.getLogger(__name__)


def run_road_network(
    engine: Engine, http_client: HttpClient, endpoint: str, path: str, parse_mode: str = "memory"
) -> dict:
    url = endpoint.rstrip("/") + path
    logger.info("road_network: fetching %s (mode=%s)", url, parse_mode)
    if parse_mode == "memory":
        rows = parse_road_network(http_client.get_json(url))
    elif parse_mode == "stream":
        rows = (parse_road_link_item(it) for it in http_client.stream_items(url, "linkCoordList.item"))
    else:
        raise ValueError(f"unknown parse_mode: {parse_mode}")
    stats = upsert_road_links(engine, rows)
    logger.info("road_network: done %s", stats)
    return stats
