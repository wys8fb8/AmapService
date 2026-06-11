"""MQTT 全量发布器:每轮路况刷新后,为每条已建线路发布地图主题(需求4)与模拟图主题(需求5)。

视图按 kind 只组装一次,再按配置的 payload_format 用一个或多个编码器各编码、各发到
原主题+后缀(json 后缀为空, protobuf 后缀默认 .pb)。单条线路/单个编码器异常被吞并记日志,
不影响其余,更不会抛回 traffic 管道。
"""
import logging
import time

from amap_service.publish.encoders import build_encoders
from amap_service.views.line_views import build_traffic_view, build_section_view
from amap_service.views.traffic_lookup import DictTrafficLookup

logger = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self, client, static_cache, cfg):
        self.client = client
        self.cache = static_cache
        self.cfg = cfg
        self.encoders = build_encoders(cfg.payload_format, cfg.pb_topic_suffix)

    def _topic(self, line_name: str, kind: str) -> str:
        return f"{self.cfg.topic_prefix}/line/{line_name}/{kind}"

    def _publish_kind(self, lookup, line: str, kind: str, stat_key: str, stats: dict) -> None:
        try:
            if kind == "traffic":
                view = build_traffic_view(self.cache, lookup, line,
                                          geometry=self.cfg.include_geometry)
            else:
                view = build_section_view(self.cache, lookup, line,
                                          geometry=self.cfg.include_geometry)
        except Exception:  # noqa: BLE001
            stats["skipped"] += 1
            logger.exception("mqtt build failed for line %s kind %s", line, kind)
            return
        if view is None:
            return
        base = self._topic(line, kind)
        encode_name = "encode_traffic" if kind == "traffic" else "encode_section"
        for enc in self.encoders:
            try:
                payload = getattr(enc, encode_name)(view)
                self.client.publish(base + enc.suffix, payload,
                                    qos=self.cfg.qos, retain=self.cfg.retain)
                stats[stat_key] += 1
            except Exception:  # noqa: BLE001
                stats["skipped"] += 1
                logger.exception("mqtt publish failed for line %s topic %s",
                                 line, base + enc.suffix)

    def publish_all(self, rows) -> dict:
        lookup = DictTrafficLookup(rows)
        stats = {"map": 0, "section": 0, "skipped": 0}
        started = time.monotonic()
        for entry in self.cache.lines():
            line = entry["line_name"]
            if self.cfg.publish_map:
                self._publish_kind(lookup, line, "traffic", "map", stats)
            if self.cfg.publish_section:
                self._publish_kind(lookup, line, "section", "section", stats)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("mqtt publish summary: format=%s map=%d section=%d skipped=%d elapsed=%dms",
                    self.cfg.payload_format, stats["map"], stats["section"],
                    stats["skipped"], elapsed_ms)
        return stats
