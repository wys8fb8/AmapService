# 设计：MQTT 发布支持 protobuf 序列化格式（瘦身线路路况订阅）

日期：2026-06-11
状态：已评审通过，待出实现计划

## 1. 背景与目标

现有 MQTT 发布（见 [2026-06-09-服务层-API与MQTT发布-design.md](2026-06-09-服务层-API与MQTT发布-design.md)）在每 2 分钟全量路况刷新后，为**每条已建线路**各发两个主题：

- `amap/line/{name}/traffic`（需求4 地图主题）——逐路段 `{seq, link_id, state, speed, travel_time, reverse}`
- `amap/line/{name}/section`（需求5 模拟图主题）——站间 `{from/to_level_id, links:[{link_id, state, pct}]}`

每轮把整条线路所有路段**完整重发**，其中 `link_id`（19 位字符串）、`seq`、`reverse`、`pct`、`from/to_level_id` 等**静态字段每轮重复**，而订阅端真正关心的只有路况变化（`state/speed/travel_time`）。JSON 文本 + 字符串化的 64 位 `link_id` 导致 payload 体积偏大。

**目标**：新增配置项，让两个主题可以用 **protobuf** 二进制格式发布，显著降低线上体积，同时保持向后兼容。

### 已确认的关键决策

| 决策 | 选择 |
|---|---|
| 序列化格式 | **protobuf**（proto3） |
| 字段范围 | **1:1 镜像现有 JSON 视图**，不做静态/动态分离（保持推拉同构） |
| 生效范围 | **仅 MQTT 发布**；HTTP API 保持 JSON 不变 |
| 格式选择 | 配置 `payload_format`：`json` / `protobuf` / `both` |
| 主题区分 | protobuf 走**独立后缀主题**（默认 `.pb`），与 JSON 主题不撞车 |
| codegen 集成 | 提交 `.proto` + 提交生成的 `*_pb2.py`，运行/测试不需 protoc |
| 向后兼容 | 默认 `payload_format: json`，行为与现状逐字节一致 |

## 2. 配置文件新增

`MqttConfig`（[amap_service/config/schema.py](../../../amap_service/config/schema.py)）新增两个字段：

```yaml
mqtt:
  # ……现有字段不变……
  payload_format: "json"      # json | protobuf | both ;默认 json,行为同现状
  pb_topic_suffix: ".pb"      # protobuf 主题在原主题名后追加的后缀
```

语义：

- `json`：只发原主题（现状行为）。
- `protobuf`：只发 `…/traffic.pb`、`…/section.pb`，原 JSON 主题不发。
- `both`：JSON 发原主题 + protobuf 发 `.pb` 主题，供新旧订阅端平滑过渡。
- `publish_map` / `publish_section` 与格式**正交**：先决定发哪个需求的主题，再决定用哪种/哪些编码。
- `include_geometry` 对两种编码统一生效（protobuf 里 `line_track` 为 optional 字段）。
- `payload_format` 用 `Literal["json","protobuf","both"]`，非法值在配置加载期由 pydantic 报错。

## 3. 主题命名与 `.proto` 契约

### 3.1 主题（`prefix` 默认 `amap`，后缀默认 `.pb`）

| 需求 | JSON 主题 | protobuf 主题 |
|---|---|---|
| 需求4 地图 | `amap/line/{name}/traffic` | `amap/line/{name}/traffic.pb` |
| 需求5 模拟图 | `amap/line/{name}/section` | `amap/line/{name}/section.pb` |

### 3.2 `.proto`（proto3，仓库 `proto/line_traffic.proto`，即订阅端契约）

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

关键点：

- **`link_id` 用 `int64`**——protobuf 原生 64 位，订阅端按 long 解码，彻底绕开 JS 的 2^53 精度坑（比现状字符串方案更省、更稳）。
- **需求4 的 `state/speed/travel_time` 用 `optional`**：JSON 里路况缺失是 `null`，proto3 标量默认 0 会和真值 0 混淆，故用显式 presence；编码时 `None` 即不 set。
- **需求5 的 `state`** 缺失约定默认 `1`（沿用 `StationTrafficResolver.default_state`），非 optional，照填即可。
- 字段编号一旦发布即冻结，演进只追加新编号。

## 4. 代码结构与序列化集成

核心原则：**视图层不变**——`build_traffic_view` / `build_section_view`（[amap_service/views/line_views.py](../../../amap_service/views/line_views.py)）仍产出 dict，推拉同构保持；只在"dict → bytes"这一步插入编码器。

### 4.1 新增模块

```
amap_service/publish/
  encoders.py             # JSON / Protobuf 编码器,统一接口
  proto/
    __init__.py
    line_traffic_pb2.py   # 由 proto/line_traffic.proto 生成并提交
```

### 4.2 编码器接口（每个编码器知道自己的主题后缀）

```python
class Encoder:
    suffix: str                       # "" (json) 或 ".pb"
    def encode_traffic(self, view: dict) -> bytes | str: ...
    def encode_section(self, view: dict) -> bytes | str: ...

class JsonEncoder(Encoder):           # suffix="";现有 json.dumps(ensure_ascii=False) 逻辑搬入
    ...
class ProtobufEncoder(Encoder):       # suffix=".pb";dict → *_pb2 message → SerializeToString()
    ...
```

`dict → protobuf` 的映射封装在 `ProtobufEncoder` 内：遍历 directions/segments|sections/links 填 message；需求4 中 `None` 字段对 optional 直接不 set。`*_pb2` 仅在构造 `ProtobufEncoder` 时 import（`json` 模式不 import，类比现有 paho NoOp 的惰性 import）。

### 4.3 发布器改造（[amap_service/publish/publisher.py](../../../amap_service/publish/publisher.py)）

- 构造时按 `payload_format` 解析出 `encoders` 列表：`json`→`[JsonEncoder]`，`protobuf`→`[ProtobufEncoder]`，`both`→两者。
- 每条线路：**视图只组装一次**，然后对列表里每个编码器各编码、各发到 `base_topic + encoder.suffix`。视图组装是大头、编码很轻，故 `both` 模式几乎不增成本。
- `client.publish` 的 `payload` 现在可能是 `str`（JSON）或 `bytes`（protobuf）；paho 两者都支持，`NoOpMqttClient.publish` 签名放宽接受 `str | bytes`。
- 汇总日志按格式分别计数：`map_json / map_pb / section_json / section_pb / skipped / elapsed_ms`。

## 5. 错误处理

沿用现有"发布/接口故障绝不拖垮需求1/2 数据落地"原则：

- 每条线路、每个编码器独立 `try/except`：某编码器编码或 publish 异常 → 跳过该线该格式、记日志、计入 `skipped`，继续其余。`both` 模式下 protobuf 失败不影响同线 JSON 的发送。
- `payload_format` 非法 → 配置加载期 pydantic 直接报错，不留运行期。
- protobuf 生成模块 import 失败只在 `protobuf`/`both` 启用时才发生（`json` 模式不触发）。
- MQTT 连接/发布异常仍被发布器吞并记日志，不抛回 traffic 管道。

## 6. 测试

沿用 `tests/publish/` 布局：

- `tests/publish/test_encoders.py`（新增）：
  - JSON 编码输出与现状逐字段一致。
  - protobuf 编码后用同一 `*_pb2` **回解**，字段与视图 dict 等价。
  - `link_id` int64 往返不丢精度（取 `5130091959790075998` 等 >2^53 值）。
  - 需求4 `state=None` 在 protobuf 里为 absent；需求5 `state` 缺失为默认 `1`。
  - `include_geometry` 开/关时 `line_track` 有/无。
- `tests/publish/test_publisher.py`（扩充）：
  - `json` / `protobuf` / `both` 三模式下，fake client 收到的主题名（含 `.pb` 后缀）与条数正确。
  - `both` 模式下视图只组装一次（对视图函数调用计数断言）。
  - 单编码器异常被吞，不影响另一编码器与其余线路。
- 回归：`payload_format` 缺省（json）时，主题名与 payload 与改造前**逐字节一致**。
- `tests/config/test_schema.py`（扩充）：`payload_format` 默认值、`both`/非法值校验。

## 7. 依赖、代码生成与契约分发

- **依赖**：`pyproject.toml` 加 `protobuf`（纯运行期，仅 `protobuf`/`both` 模式真正使用）。`protoc` 仅开发期工具，不进运行/CI 依赖。
- **代码生成**：`proto/line_traffic.proto` 为唯一源，生成 `amap_service/publish/proto/line_traffic_pb2.py` 一并提交。新增重生成脚本（`scripts/gen_proto.sh` 或 Makefile `make proto`，内容即 `protoc --python_out=… proto/line_traffic.proto`），README/命令说明注明"改 `.proto` 后要重跑"。
- **契约分发**：`.proto` 即订阅端接口契约，订阅方据此各自生成解码代码（JS/Java/Go/Python 等）。

## 8. 文档同步

- [docs/命令说明.md](../../命令说明.md) MQTT 章节补一节：三种 `payload_format`、`.pb` 主题命名、`link_id` 为 int64、需求4 三字段为 optional，并指向 `proto/line_traffic.proto`。
- 既有发布设计文档 [2026-06-09-服务层-API与MQTT发布-design.md](2026-06-09-服务层-API与MQTT发布-design.md) 的"不做(YAGNI)"项作相应更新（原"不做序列化优化"类表述）。

## 9. 不做（YAGNI）

- 不做静态/动态主题分离或 delta 增量发布（本次只换线上编码，保持 1:1 同构）。
- 不做 HTTP API 的 protobuf 协商（仅 MQTT）。
- 不做 MessagePack / gzip 等其他格式（已选定 protobuf）。
- 不做 protobuf schema 版本协商通道（靠 `.proto` 字段编号冻结 + 后缀主题隔离即可）。
