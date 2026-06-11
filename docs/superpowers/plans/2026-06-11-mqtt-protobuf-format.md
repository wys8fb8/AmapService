# MQTT protobuf 发布格式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 MQTT 发布器除现有 JSON 外，可按配置以 protobuf 二进制格式发布两个线路主题，显著降低订阅端 payload 体积。

**Architecture:** 视图层（`build_traffic_view` / `build_section_view`）保持不变，仍产出 dict；在 "dict → bytes" 这一步插入一个编码器列表（`JsonEncoder` / `ProtobufEncoder`），每个编码器自带主题后缀。发布器按 `payload_format`（`json`/`protobuf`/`both`）构造编码器列表，每条线路的视图只组装一次，再对每个编码器各编码、各发到 `原主题 + 后缀`。仅作用于 MQTT，HTTP API 不变。

**Tech Stack:** Python 3.11、pydantic v2、protobuf（runtime）、grpcio-tools（开发期生成 `*_pb2.py`）、paho-mqtt、pytest。

参考 spec：[docs/superpowers/specs/2026-06-11-MQTT-protobuf发布格式-design.md](../specs/2026-06-11-MQTT-protobuf发布格式-design.md)

---

## File Structure

- `amap_service/config/schema.py` — 修改 `MqttConfig`，新增 `payload_format`、`pb_topic_suffix`。
- `proto/line_traffic.proto` — 新建，protobuf 契约源（订阅端契约）。
- `scripts/gen_proto.sh` — 新建，重生成 `*_pb2.py` 的脚本。
- `amap_service/publish/proto/__init__.py` — 新建，包标记。
- `amap_service/publish/proto/line_traffic_pb2.py` — 由 protoc 生成并提交（勿手改）。
- `amap_service/publish/encoders.py` — 新建，`Encoder` 接口、`JsonEncoder`、`ProtobufEncoder`、`build_encoders()`。
- `amap_service/publish/publisher.py` — 修改，改用编码器列表 + 后缀主题 + 逐编码器容错。
- `pyproject.toml` — 修改，加 `protobuf` 运行期依赖、`grpcio-tools` 开发期依赖。
- `config/config.yaml` — 修改 `mqtt` 段，加示例字段。
- `docs/命令说明.md` — 修改 MQTT 章节，补 protobuf 说明。
- 测试：`tests/config/test_schema.py`（扩充）、`tests/publish/test_encoders.py`（新建）、`tests/publish/test_publisher.py`（扩充）。

---

## Task 1: 配置新增 `payload_format` 与 `pb_topic_suffix`

**Files:**
- Modify: `amap_service/config/schema.py:165-181` (`MqttConfig`)
- Test: `tests/config/test_schema.py`

- [ ] **Step 1: 写失败测试**

在 `tests/config/test_schema.py` 末尾追加：

```python
def test_mqtt_payload_format_defaults():
    from amap_service.config.schema import MqttConfig
    cfg = MqttConfig()
    assert cfg.payload_format == "json"
    assert cfg.pb_topic_suffix == ".pb"


def test_mqtt_payload_format_accepts_protobuf_and_both():
    from amap_service.config.schema import MqttConfig
    assert MqttConfig(payload_format="protobuf").payload_format == "protobuf"
    assert MqttConfig(payload_format="both").payload_format == "both"


def test_mqtt_payload_format_invalid_rejected():
    import pytest
    from pydantic import ValidationError
    from amap_service.config.schema import MqttConfig
    with pytest.raises(ValidationError):
        MqttConfig(payload_format="msgpack")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/config/test_schema.py::test_mqtt_payload_format_defaults -v`
Expected: FAIL（`MqttConfig` 无 `payload_format` 属性 / AttributeError 或断言失败）

- [ ] **Step 3: 改 `MqttConfig`**

在 `amap_service/config/schema.py` 顶部确认 `Literal` 已从 typing 导入（若无则加 `from typing import Literal`）。
在 `MqttConfig` 内 `connect_timeout_seconds: int = 5` 之后、`static_cache_ttl_seconds` 之前插入：

```python
    # 线上编码格式:json=仅原主题; protobuf=仅 .pb 主题; both=两者各发一份(平滑过渡)
    payload_format: Literal["json", "protobuf", "both"] = "json"
    pb_topic_suffix: str = ".pb"   # protobuf 主题在原主题名后追加的后缀
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/config/test_schema.py -v`
Expected: PASS（全部，含既有用例）

- [ ] **Step 5: 提交**

```bash
git add amap_service/config/schema.py tests/config/test_schema.py
git commit -m "feat(config): MqttConfig 新增 payload_format/pb_topic_suffix"
```

---

## Task 2: protobuf 契约、依赖与代码生成

**Files:**
- Create: `proto/line_traffic.proto`
- Create: `scripts/gen_proto.sh`
- Create: `amap_service/publish/proto/__init__.py`
- Generate: `amap_service/publish/proto/line_traffic_pb2.py`
- Modify: `pyproject.toml:6-18`（dependencies）、`pyproject.toml:23`（dev extras）

- [ ] **Step 1: 写 `.proto` 契约**

新建 `proto/line_traffic.proto`：

```protobuf
syntax = "proto3";
package amap.line.v1;

message TrafficView {                 // 需求4 地图主题
  string line_name = 1;
  string traffic_time = 2;            // 与 JSON 1:1,东八区字符串
  repeated TrafficDirection directions = 3;
}
message TrafficDirection {
  int32 direction = 1;
  repeated TrafficSegment segments = 2;
}
message TrafficSegment {
  int32  seq = 1;
  int64  link_id = 2;                 // 64 位原生,不再用字符串
  optional int32 state = 3;           // optional 保留"路况缺失"语义(区别于 0)
  optional int32 speed = 4;
  optional int32 travel_time = 5;
  int32  reverse = 6;
  optional string line_track = 7;     // include_geometry=true 才填
}

message SectionView {                 // 需求5 模拟图主题
  string line_name = 1;
  string traffic_time = 2;
  repeated SectionDirection directions = 3;
}
message SectionDirection {
  int32 direction = 1;
  repeated Section sections = 2;
}
message Section {
  int32 from_level_id = 1;
  int32 to_level_id = 2;
  repeated SectionLink links = 3;
}
message SectionLink {
  int64  link_id = 1;
  int32  state = 2;                   // 需求5 缺失默认 1,故非 optional
  int32  pct = 3;
  optional string line_track = 4;
}
```

- [ ] **Step 2: 加依赖到 `pyproject.toml`**

在 `dependencies` 列表里（`"paho-mqtt>=1.6,<2",` 之后）加一行（注：用 `>=5`，因 grpcio-tools 5.x 生成的 `*_pb2` 内嵌 protobuf 5.x runtime 版本校验，4.x 无 `runtime_version` 模块会 ImportError）：

```toml
  "protobuf>=5,<6",
```

把 `dev` extras 改为含生成工具：

```toml
dev = ["pytest>=8", "fakeredis>=2", "grpcio-tools>=1.66"]
```

- [ ] **Step 3: 创建生成脚本与包标记**

新建 `scripts/gen_proto.sh`：

```bash
#!/usr/bin/env bash
# 由 proto/line_traffic.proto 重新生成 amap_service/publish/proto/line_traffic_pb2.py。
# 改了 .proto 后必须重跑本脚本。需先安装 dev 依赖(含 grpcio-tools)。
set -euo pipefail
cd "$(dirname "$0")/.."
python -m grpc_tools.protoc -I proto \
  --python_out=amap_service/publish/proto \
  proto/line_traffic.proto
echo "generated amap_service/publish/proto/line_traffic_pb2.py"
```

新建 `amap_service/publish/proto/__init__.py`（空文件，作为包标记）：

```python
```

- [ ] **Step 4: 安装工具并生成 `*_pb2.py`**

Run:
```bash
pip install -e ".[dev]"
chmod +x scripts/gen_proto.sh
./scripts/gen_proto.sh
```
Expected: 输出 `generated amap_service/publish/proto/line_traffic_pb2.py`，且文件 `amap_service/publish/proto/line_traffic_pb2.py` 存在。

- [ ] **Step 5: 验证生成模块可导入并具备消息类型**

Run:
```bash
python -c "from amap_service.publish.proto import line_traffic_pb2 as pb; print(pb.TrafficView, pb.SectionView)"
```
Expected: 打印两个消息类（类似 `<class '...TrafficView'> <class '...SectionView'>`），无 ImportError。

- [ ] **Step 6: 提交**

```bash
git add proto/line_traffic.proto scripts/gen_proto.sh \
  amap_service/publish/proto/__init__.py \
  amap_service/publish/proto/line_traffic_pb2.py pyproject.toml
git commit -m "feat(proto): 新增 line_traffic.proto 契约与生成的 _pb2,加 protobuf 依赖"
```

---

## Task 3: 编码器（JSON / Protobuf）

**Files:**
- Create: `amap_service/publish/encoders.py`
- Test: `tests/publish/test_encoders.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/publish/test_encoders.py`：

```python
import json

from amap_service.publish.encoders import (
    JsonEncoder, ProtobufEncoder, build_encoders,
)
from amap_service.publish.proto import line_traffic_pb2 as pb

LID = 5130091959790075998  # > 2**53,验证 int64 不丢精度


def _traffic_view(state=2, geometry=False):
    seg = {"seq": 0, "link_id": str(LID), "state": state,
           "speed": 18, "travel_time": 35, "reverse": 0}
    if geometry:
        seg["line_track"] = "121.1,31.1;121.2,31.2"
    return {"line_name": "47", "traffic_time": "2026-06-09 13:02:00",
            "directions": [{"direction": 0, "segments": [seg]}]}


def _section_view():
    return {"line_name": "47", "traffic_time": "2026-06-09 13:02:00",
            "directions": [{"direction": 0, "sections": [
                {"from_level_id": 1, "to_level_id": 2,
                 "links": [{"link_id": str(LID), "state": 1, "pct": 100}]}]}]}


def test_build_encoders_modes():
    assert [type(e).__name__ for e in build_encoders("json", ".pb")] == ["JsonEncoder"]
    assert [type(e).__name__ for e in build_encoders("protobuf", ".pb")] == ["ProtobufEncoder"]
    assert [type(e).__name__ for e in build_encoders("both", ".pb")] == \
        ["JsonEncoder", "ProtobufEncoder"]


def test_json_encoder_matches_plain_dumps():
    enc = JsonEncoder()
    assert enc.suffix == ""
    view = _traffic_view()
    assert enc.encode_traffic(view) == json.dumps(view, ensure_ascii=False)


def test_protobuf_traffic_roundtrip_int64_and_optional():
    enc = ProtobufEncoder(".pb")
    assert enc.suffix == ".pb"
    raw = enc.encode_traffic(_traffic_view())
    assert isinstance(raw, bytes)
    msg = pb.TrafficView()
    msg.ParseFromString(raw)
    seg = msg.directions[0].segments[0]
    assert seg.link_id == LID          # int64 精度无损
    assert seg.state == 2 and seg.speed == 18 and seg.travel_time == 35
    assert msg.line_name == "47"


def test_protobuf_traffic_missing_state_is_absent():
    raw = ProtobufEncoder(".pb").encode_traffic(_traffic_view(state=None))
    msg = pb.TrafficView()
    msg.ParseFromString(raw)
    seg = msg.directions[0].segments[0]
    assert seg.HasField("state") is False   # None → 不 set(区别于真值 0)


def test_protobuf_geometry_toggle():
    msg = pb.TrafficView()
    msg.ParseFromString(ProtobufEncoder(".pb").encode_traffic(_traffic_view(geometry=False)))
    assert msg.directions[0].segments[0].HasField("line_track") is False
    msg2 = pb.TrafficView()
    msg2.ParseFromString(ProtobufEncoder(".pb").encode_traffic(_traffic_view(geometry=True)))
    assert msg2.directions[0].segments[0].line_track == "121.1,31.1;121.2,31.2"


def test_protobuf_section_roundtrip():
    raw = ProtobufEncoder(".pb").encode_section(_section_view())
    msg = pb.SectionView()
    msg.ParseFromString(raw)
    sec = msg.directions[0].sections[0]
    assert sec.from_level_id == 1 and sec.to_level_id == 2
    lk = sec.links[0]
    assert lk.link_id == LID and lk.state == 1 and lk.pct == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/publish/test_encoders.py -v`
Expected: FAIL（`ModuleNotFoundError: amap_service.publish.encoders`）

- [ ] **Step 3: 实现 `encoders.py`**

新建 `amap_service/publish/encoders.py`：

```python
"""推送 payload 编码器:把视图 dict 编码为 MQTT payload。

视图层(line_views)产出 dict 不变;本层负责 dict→bytes/str,并各自声明主题后缀。
JsonEncoder 行为与改造前 json.dumps 逐字节一致;ProtobufEncoder 惰性 import 生成模块
(json 模式不触发 protobuf 依赖)。link_id 在视图里被字符串化,这里转回 int 填 int64 字段。
"""
import json


class JsonEncoder:
    suffix = ""

    def encode_traffic(self, view: dict) -> str:
        return json.dumps(view, ensure_ascii=False)

    def encode_section(self, view: dict) -> str:
        return json.dumps(view, ensure_ascii=False)


class ProtobufEncoder:
    def __init__(self, suffix: str):
        self.suffix = suffix
        from amap_service.publish.proto import line_traffic_pb2 as pb
        self._pb = pb

    def encode_traffic(self, view: dict) -> bytes:
        pb = self._pb
        msg = pb.TrafficView(line_name=view["line_name"])
        if view.get("traffic_time") is not None:
            msg.traffic_time = view["traffic_time"]
        for d in view["directions"]:
            dr = msg.directions.add()
            dr.direction = d["direction"]
            for s in d["segments"]:
                seg = dr.segments.add()
                seg.seq = s["seq"]
                seg.link_id = int(s["link_id"])
                if s.get("state") is not None:
                    seg.state = s["state"]
                if s.get("speed") is not None:
                    seg.speed = s["speed"]
                if s.get("travel_time") is not None:
                    seg.travel_time = s["travel_time"]
                seg.reverse = s["reverse"]
                if s.get("line_track") is not None:
                    seg.line_track = s["line_track"]
        return msg.SerializeToString()

    def encode_section(self, view: dict) -> bytes:
        pb = self._pb
        msg = pb.SectionView(line_name=view["line_name"])
        if view.get("traffic_time") is not None:
            msg.traffic_time = view["traffic_time"]
        for d in view["directions"]:
            dr = msg.directions.add()
            dr.direction = d["direction"]
            for sec in d["sections"]:
                so = dr.sections.add()
                so.from_level_id = sec["from_level_id"]
                so.to_level_id = sec["to_level_id"]
                for lk in sec["links"]:
                    lo = so.links.add()
                    lo.link_id = int(lk["link_id"])
                    lo.state = lk["state"]
                    lo.pct = lk["pct"]
                    if lk.get("line_track") is not None:
                        lo.line_track = lk["line_track"]
        return msg.SerializeToString()


def build_encoders(payload_format: str, pb_suffix: str) -> list:
    encoders = []
    if payload_format in ("json", "both"):
        encoders.append(JsonEncoder())
    if payload_format in ("protobuf", "both"):
        encoders.append(ProtobufEncoder(pb_suffix))
    return encoders
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/publish/test_encoders.py -v`
Expected: PASS（全部）

- [ ] **Step 5: 提交**

```bash
git add amap_service/publish/encoders.py tests/publish/test_encoders.py
git commit -m "feat(publish): 新增 JSON/Protobuf 编码器与 build_encoders"
```

---

## Task 4: 发布器改用编码器列表 + 后缀主题 + 逐编码器容错

**Files:**
- Modify: `amap_service/publish/publisher.py:16-54`
- Test: `tests/publish/test_publisher.py`

设计要点（务必照此实现，后续测试依赖这些约定）：
- `MqttPublisher.__init__` 用 `build_encoders(cfg.payload_format, cfg.pb_topic_suffix)` 建 `self.encoders`。
- 每条线路每个 kind（traffic/section）视图**只组装一次**；视图组装失败 → `skipped += 1` 并跳过该 kind。
- 视图 `None`（线路无该类数据）→ 跳过，不计 skipped。
- 对每个编码器：编码 + publish 到 `base_topic + enc.suffix`；成功 `stats[map|section] += 1`；该编码器抛异常 → `skipped += 1`，不影响同线其它编码器与其它线路。
- `stats` 仍为 `{"map", "section", "skipped"}`：map/section 是 traffic/section 主题的发布**总条数**（跨格式累加），故 json 模式下与现状一致（1/1/0），both 模式自然翻倍。

- [ ] **Step 1: 写/改失败测试**

把 `tests/publish/test_publisher.py` 顶部的 `FakeMqttClient` 改为不假设 payload 是 JSON（同时保留一个 JSON 解析辅助），并在文件末尾追加 protobuf/both/隔离用例。

将第 29-34 行的 `FakeMqttClient` 替换为：

```python
class FakeMqttClient:
    def __init__(self):
        self.published = []  # [(topic, raw_payload, qos, retain)]

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
```

由于 `published` 现在存原始 payload，需把现有用例中按 dict 取用的地方改为 JSON 解析。具体修改：

`test_lean_payload_default_no_geometry` 改为：

```python
def test_lean_payload_default_no_geometry():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, include_geometry=False))
    pub.publish_all(_rows())
    raw = next(p for t, p, _, _ in client.published if t.endswith("/traffic"))
    traffic = json.loads(raw)
    assert "line_track" not in traffic["directions"][0]["segments"][0]
```

`test_geometry_flag_includes_track` 改为：

```python
def test_geometry_flag_includes_track():
    client = FakeMqttClient()
    pub = MqttPublisher(client, FakeStaticCache(), MqttConfig(enabled=True, include_geometry=True))
    pub.publish_all(_rows())
    raw = next(p for t, p, _, _ in client.published if t.endswith("/traffic"))
    traffic = json.loads(raw)
    assert traffic["directions"][0]["segments"][0]["line_track"] == "121.1,31.1"
```

`test_one_line_failure_does_not_abort_others` 内 `FlakyClient.publish` 改为不调用 `json.loads`：

```python
    class FlakyClient(FakeMqttClient):
        def publish(self, topic, payload, qos=0, retain=False):
            if "boom" in topic:
                raise RuntimeError("broker down")
            super().publish(topic, payload, qos, retain)
```

（`test_publishes_both_topics_per_line`、`test_qos_and_retain_from_config`、`test_publish_map_section_toggles` 不解析 payload，保持不变即可。）

在文件末尾追加：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/publish/test_publisher.py -v`
Expected: FAIL（`test_protobuf_mode_publishes_pb_topics_only` 等失败——当前发布器忽略 `payload_format`，不发 `.pb` 主题）

- [ ] **Step 3: 重写 `publisher.py`**

整体替换 `amap_service/publish/publisher.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/publish/ -v`
Expected: PASS（含既有 6 个 + 新增 3 个）

- [ ] **Step 5: 跑全量回归确认 json 默认行为未变**

Run: `pytest -q`
Expected: PASS（全部）

- [ ] **Step 6: 提交**

```bash
git add amap_service/publish/publisher.py tests/publish/test_publisher.py
git commit -m "feat(publish): 发布器按 payload_format 多编码器输出,逐编码器容错"
```

---

## Task 5: 配置示例与文档同步

**Files:**
- Modify: `config/config.yaml:121-135`（mqtt 段）
- Modify: `docs/命令说明.md`（MQTT 章节）

- [ ] **Step 1: 更新 `config/config.yaml` 示例**

在 `config/config.yaml` 的 `mqtt:` 段，`publish_section: true` 那一行之后插入：

```yaml
  payload_format: "json"      # json | protobuf | both ;默认 json(行为同现状)
  pb_topic_suffix: ".pb"      # protobuf 主题在原主题名后追加的后缀(如 amap/line/47/traffic.pb)
```

- [ ] **Step 2: 更新 `docs/命令说明.md` MQTT 章节**

在 `docs/命令说明.md` 中 MQTT 相关章节追加一小节（紧随主题/payload 说明之后）：

```markdown
#### 发布格式（protobuf）

`mqtt.payload_format` 控制两个线路主题的线上编码：

- `json`（默认）：仅发原主题 `amap/line/{line}/traffic`、`amap/line/{line}/section`。
- `protobuf`：仅发后缀主题 `amap/line/{line}/traffic.pb`、`…/section.pb`（后缀由 `pb_topic_suffix` 配置，默认 `.pb`）。
- `both`：JSON 与 protobuf 各发一份，便于新旧订阅端平滑过渡。

protobuf 契约见 [proto/line_traffic.proto](../proto/line_traffic.proto)，订阅端据此自行生成解码代码。要点：
`link_id` 为 `int64`（不再字符串化，订阅端按 long 解码即可，无 2^53 精度坑）；需求4 的
`state/speed/travel_time` 为 `optional`，路况缺失时字段缺省（区别于真值 0）；需求5 的 `state`
缺失默认 `1`。改动 `.proto` 后需运行 `./scripts/gen_proto.sh` 重新生成 `*_pb2.py`。
```

（若 `docs/命令说明.md` 现有 MQTT 章节标题层级不同，按其层级把上面的 `####` 调整为相应级别。）

- [ ] **Step 3: 提交**

```bash
git add config/config.yaml docs/命令说明.md
git commit -m "docs: 补充 MQTT payload_format(protobuf) 配置与契约说明"
```

---

## Self-Review

**Spec coverage：**
- §2 配置新增（payload_format / pb_topic_suffix）→ Task 1。
- §3 主题命名 + `.proto` 契约 → Task 2（含 link_id int64、optional 字段）。
- §4 代码结构（视图不变、编码器接口、发布器只组装一次 + 后缀主题）→ Task 3 + Task 4。
- §5 错误处理（逐编码器/逐 kind 容错、非法格式配置期报错、protobuf 惰性 import）→ Task 1（Literal 校验）+ Task 3（惰性 import）+ Task 4（容错）。
- §6 测试（encoders 往返/int64/optional/geometry；publisher 三模式/隔离；config 默认与非法值；json 回归）→ Task 1/3/4 测试步骤。
- §7 依赖、代码生成、契约分发 → Task 2。
- §8 文档同步（命令说明、配置示例）→ Task 5。
- §9 YAGNI（不做静态/动态分离、不动 API、不做其它格式）→ 计划范围未触及,符合。

**Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整代码；命令含预期输出。

**类型/命名一致性：** 编码器方法 `encode_traffic` / `encode_section`、属性 `suffix`、`build_encoders(payload_format, pb_suffix)`、`MqttPublisher.encoders`、stats 键 `map/section/skipped`、配置字段 `payload_format`/`pb_topic_suffix` 在 Task 1→4 中前后一致。生成模块路径 `amap_service.publish.proto.line_traffic_pb2` 在 Task 2/3/4 一致。

**注意（spec 微调）：** spec §4.3 提到日志按 `map_json/map_pb/...` 分格式计数；为保持 `stats` 与既有测试兼容，本计划将 `stats` 维持 `map/section/skipped`（跨格式累加）并在日志行追加 `format=` 字段，语义等价且更简洁——实现时以本计划为准。
