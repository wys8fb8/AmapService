"""MQTT 全量发布器:每轮路况刷新后,为每条已建线路发布地图主题(需求4)与模拟图主题(需求5)。

路况取自 traffic 管道的内存全量 rows(DictTrafficLookup,零回读)。单条线路组装/发布异常被吞并记日志,
不影响其余线路,更不会抛回 traffic 管道。
"""
import json
import logging
import time

from amap_service.views.line_views import build_traffic_view, build_section_view
from amap_service.views.traffic_lookup import DictTrafficLookup

logger = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self, client, static_cache, cfg):
        self.client = client
        self.cache = static_cache
        self.cfg = cfg

    def _topic(self, line_name: str, kind: str) -> str:
        return f"{self.cfg.topic_prefix}/line/{line_name}/{kind}"

    def _emit(self, topic: str, view: dict) -> None:
        payload = json.dumps(view, ensure_ascii=False)
        self.client.publish(topic, payload, qos=self.cfg.qos, retain=self.cfg.retain)

    def publish_all(self, rows) -> dict:
        lookup = DictTrafficLookup(rows)
        stats = {"map": 0, "section": 0, "skipped": 0}
        started = time.monotonic()
        for entry in self.cache.lines():
            line = entry["line_name"]
            try:
                if self.cfg.publish_map:
                    view = build_traffic_view(self.cache, lookup, line,
                                              geometry=self.cfg.include_geometry)
                    if view is not None:
                        self._emit(self._topic(line, "traffic"), view)
                        stats["map"] += 1
                if self.cfg.publish_section:
                    view = build_section_view(self.cache, lookup, line,
                                              geometry=self.cfg.include_geometry)
                    if view is not None:
                        self._emit(self._topic(line, "section"), view)
                        stats["section"] += 1
            except Exception:  # noqa: BLE001
                stats["skipped"] += 1
                logger.exception("mqtt publish failed for line %s", line)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("mqtt publish summary: map=%d section=%d skipped=%d elapsed=%dms",
                    stats["map"], stats["section"], stats["skipped"], elapsed_ms)
        return stats
