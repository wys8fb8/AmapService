import json

from amap_service.config.schema import MqttConfig
from amap_service.publish.publisher import MqttPublisher

LID = 5130091959790075998


class FakeStaticCache:
    def lines(self):
        return [{"line_name": "47", "directions": [0],
                 "has_segments": True, "has_sections": True, "station_count": 2}]

    def segments(self, line):
        if line != "47":
            return {}
        return {0: [{"seq": 0, "link_id": LID, "reverse": 0, "line_track": "121.1,31.1"}]}

    def sections(self, line):
        if line != "47":
            return {}
        return {0: [{"from_level_id": 1, "to_level_id": 2,
                     "links": [{"link_id": LID, "length_m": 100.0, "pct": 100}]}]}

    def link_track(self, link_id):
        return "121.1,31.1"


class FakeMqttClient:
    def __init__(self):
        self.published = []  # [(topic, raw_payload, qos, retain)]

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _rows():
    return [{"link_id": LID, "state": 2, "speed": 18, "travel_time": 35,
             "traffic_time": "2026-06-09 13:02:00"}]


def test_publishes_both_topics_per_line():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, topic_prefix="amap"))
    stats = pub.publish_all(_rows())
    topics = {t for t, _, _, _ in client.published}
    assert topics == {"amap/line/47/traffic", "amap/line/47/section"}
    assert stats["map"] == 1 and stats["section"] == 1 and stats["skipped"] == 0


def test_qos_and_retain_from_config():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, qos=1, retain=True))
    pub.publish_all(_rows())
    _, _, qos, retain = client.published[0]
    assert qos == 1 and retain is True


def test_lean_payload_default_no_geometry():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, include_geometry=False))
    pub.publish_all(_rows())
    raw = next(p for t, p, _, _ in client.published if t.endswith("/traffic"))
    traffic = json.loads(raw)
    assert "line_track" not in traffic["directions"][0]["segments"][0]


def test_geometry_flag_includes_track():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, include_geometry=True))
    pub.publish_all(_rows())
    raw = next(p for t, p, _, _ in client.published if t.endswith("/traffic"))
    traffic = json.loads(raw)
    assert traffic["directions"][0]["segments"][0]["line_track"] == "121.1,31.1"


def test_publish_map_section_toggles():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(),
                        MqttConfig(enabled=True, publish_section=False))
    pub.publish_all(_rows())
    topics = {t for t, _, _, _ in client.published}
    assert topics == {"amap/line/47/traffic"}


def test_one_line_failure_does_not_abort_others():
    class FlakyClient(FakeMqttClient):
        def publish(self, topic, payload, qos=0, retain=False):
            if "boom" in topic:
                raise RuntimeError("broker down")
            super().publish(topic, payload, qos, retain)

    class TwoLineCache(FakeStaticCache):
        def lines(self):
            return [{"line_name": "boom", "directions": [0], "has_segments": True,
                     "has_sections": False, "station_count": 0},
                    {"line_name": "47", "directions": [0], "has_segments": True,
                     "has_sections": True, "station_count": 2}]
        def segments(self, line):
            return {0: [{"seq": 0, "link_id": LID, "reverse": 0, "line_track": "x"}]}

    client = FlakyClient()
    pub = MqttPublisher(client, TwoLineCache(), MqttConfig(enabled=True))
    stats = pub.publish_all(_rows())
    assert any(t == "amap/line/47/traffic" for t, _, _, _ in client.published)
    assert stats["skipped"] >= 1


from amap_service.publish.proto import line_traffic_pb2 as pb


def test_protobuf_mode_publishes_pb_topics_only():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(),
                        MqttConfig(enabled=True, payload_format="protobuf"))
    stats = pub.publish_all(_rows())
    topics = {t for t, _, _, _ in client.published}
    assert topics == {"amap/line/47/traffic.pb", "amap/line/47/section.pb"}
    raw = next(p for t, p, _, _ in client.published if t == "amap/line/47/traffic.pb")
    assert isinstance(raw, bytes)
    msg = pb.TrafficView()
    msg.ParseFromString(raw)
    assert msg.directions[0].segments[0].link_id == LID
    assert stats["map"] == 1 and stats["section"] == 1


def test_both_mode_publishes_json_and_pb():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(),
                        MqttConfig(enabled=True, payload_format="both"))
    stats = pub.publish_all(_rows())
    topics = {t for t, _, _, _ in client.published}
    assert topics == {"amap/line/47/traffic", "amap/line/47/traffic.pb",
                      "amap/line/47/section", "amap/line/47/section.pb"}
    assert stats["map"] == 2 and stats["section"] == 2 and stats["skipped"] == 0


def test_one_encoder_failure_isolated_in_both_mode():
    # JSON 主题(无后缀)发送失败,protobuf(.pb)仍应成功
    class JsonOnlyFlaky(FakeMqttClient):
        def publish(self, topic, payload, qos=0, retain=False):
            if topic.endswith("/traffic"):  # 仅 JSON traffic 主题失败
                raise RuntimeError("broker down")
            super().publish(topic, payload, qos, retain)

    client = JsonOnlyFlaky()
    pub = MqttPublisher(client, FakeStaticCache(),
                        MqttConfig(enabled=True, payload_format="both"))
    stats = pub.publish_all(_rows())
    topics = {t for t, _, _, _ in client.published}
    assert "amap/line/47/traffic.pb" in topics   # protobuf 未受 JSON 失败牵连
    assert "amap/line/47/traffic" not in topics
    assert stats["skipped"] >= 1
