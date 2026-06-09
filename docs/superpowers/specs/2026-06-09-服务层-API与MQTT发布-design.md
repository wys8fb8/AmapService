# 服务层设计：HTTP API + MQTT 发布（需求3/4/5 对外暴露）

日期：2026-06-09
状态：已评审通过，待出实现计划

## 1. 背景与目标

项目已完成需求1/2/3 的数据落地与 SDK：
- 需求1：路网 / 实时路况落地（每天 01:00 / 每 2 分钟）。
- 需求2：公交线路加工 → `transit_segment`（逐路段）、`transit_station`（站级）、`transit_section_link`（站间各路段长度占比）。
- 需求3 SDK：`StationTrafficResolver`（站间占比+路况）、`TrafficReader`（按 link 热读实时路况，Redis→DB 回落）。

**缺的是"对外服务层"**。本设计新增两条对外通道，复用已有 SDK 与 Redis 热缓存：

- **HTTP API**（需求3/4/5）：地图应用拉取线路静态路段、线路实时路况、站间占比+路况。
- **MQTT 发布**（需求4/5）：每 2 分钟全量路况刷新后，为每条已建线路全量发布两个主题——线路地图主题（逐路段路况）与线路模拟图主题（站间占比+路况）。订阅即可获取。

### 已确认的关键决策

| 决策 | 选择 |
|---|---|
| 进程拓扑 | API 独立进程（`amap-service serve`）；MQTT 发布器挂在现有 traffic 定时任务内 |
| 发布范围 | 所有已建线路，每轮全量发布 |
| payload 粒度 | 配置 `include_geometry` 控制是否带几何，默认瘦（仅状态） |
| MQTT 投递 | QoS 0，retain=false（即发即忘） |
| API 鉴权 | 配置开关，默认关；开启时校验静态 API-Key |
| 规模 | 几百条线路 × 双方向 |
| Redis 路况 | 全量镜像：每轮把全量路况写 Redis，API 几乎不回查 DB |

## 2. 架构与数据流

### 2.1 模块划分

新增三个包。`views` 是"推拉同构"的关键——API（拉）与 MQTT（推）共用同一套视图组装代码，保证字段结构一致。

```
amap_service/
  views/                  # 共享视图构建层(推/拉复用,保证字段一致)
    __init__.py
    line_views.py         # build_traffic_view() 需求4 / build_section_view() 需求5 / build_segment_view() 需求3
    static_cache.py       # segment+section_link 静态结构的内存缓存(max(built_at) 探针失效)
  api/                    # 需求3/4/5 HTTP 接口(独立进程: amap-service serve)
    __init__.py
    app.py                # FastAPI 工厂
    routes.py             # 路由
    schemas.py            # pydantic 响应模型
    deps.py               # engine/cache/config/resolver 注入
    auth.py               # API-Key 依赖(配置开关)
  publish/                # MQTT 发布器(挂在 traffic 定时任务内)
    __init__.py
    client.py             # paho 封装: connect/publish, NoOp(禁用时)
    publisher.py          # 全量发布: 遍历已建线路 → 视图层组装 → publish
```

### 2.2 数据流（每 2 分钟一轮）

```
traffic 定时任务
  fetch /traffic/status
    → parse 全量 rows (完整一份, 留内存)
    → incremental 算出变更子集 → upsert DB (仅作 DB 写优化)
    → DB 成功后: 全量 rows 写 Redis snapshot (traffic:latest:{id}, TTL 600s = 全量镜像)
    → on_complete(全量 rows) 回调 → MQTT 发布器:
         static_cache 取已建线路结构(命中内存)
         逐线路 → views.build_traffic_view / build_section_view (用内存 rows, 零回读)
         publish 两个主题 (QoS0, retain=false)

API (独立进程 amap-service serve)
  GET 需求3  → static_cache (segment 静态结构)
  GET 需求4  → views.build_traffic_view(line, static, TrafficReader)   # TrafficReader: Redis→DB
  GET 需求5  → views.build_section_view(line, static, TrafficReader)
```

### 2.3 解耦方式

- `run_traffic` 增加可选参数 `on_complete(all_rows)`；scheduler 在 MQTT 启用时注入发布器回调，禁用时传 `None`。**traffic 管道本身不认识 MQTT。**
- 视图层 `build_*_view(line, static, traffic_lookup)` 接受一个 `traffic_lookup`（可以是内存 `dict[link_id]->row`，也可以是 `TrafficReader`）。发布器传内存全量 dict；API 传 `TrafficReader`。**同一套组装代码，推拉同构。**

### 2.4 与"全量路况"语义的对齐（重要）

现有 traffic 管道开了 `incremental`，会把全量 rows **过滤成本轮变更子集**再 upsert DB、再写 Redis snapshot。若发布器直接用管道末尾的 rows，全量发布会漏掉本轮未变的 link。

**修正**：解析出的**全量** rows 留一份在内存：
- `incremental` 仅决定**哪些写 DB**（DB 写入优化，行为不变）。
- **全量** rows 写 Redis snapshot → Redis 成为完整最新镜像。
- 发布器用这份**全量**内存 rows 做全量发布（零回读）。
- API 从 Redis 读完整镜像，个别 key 过期才回落 DB。

DB 写入成功后才推进 Redis 快照（沿用现有"写成功才推进签名/快照"的修复逻辑）。

## 3. 配置文件新增

新增 pydantic 模型 `ApiConfig`、`MqttConfig`，挂到 `AppConfig`。

```yaml
# ── HTTP API（需求3/4/5，独立进程 amap-service serve）──────
api:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  auth:
    enabled: false            # 默认关；内网可不开
    api_key: ""               # 建议用环境变量 AMAP__API__AUTH__API_KEY 覆盖
    header: "X-API-Key"
  static_cache_ttl_seconds: 300   # 静态结构(segment/section)进程内缓存 TTL；0=不缓存

# ── MQTT 发布（需求4/5，挂在 traffic 定时任务内）────────────
mqtt:
  enabled: false              # 启用才发布；不启用 traffic 任务行为不变
  host: "127.0.0.1"
  port: 1883
  username: ""
  password: ""
  client_id: "amap-publisher"
  topic_prefix: "amap"        # 主题前缀
  qos: 0
  retain: false
  include_geometry: false     # 默认瘦 payload；true 则主题里带 line_track 坐标
  publish_map: true           # 需求4 线路地图主题开关
  publish_section: true       # 需求5 线路模拟图主题开关
  connect_timeout_seconds: 5
```

设计点：
- `mqtt.enabled=false` 时 `on_complete` 注入 `None`，全量路况落地行为与现状逐字节一致；MQTT 是纯增量功能。
- `api.enabled` 仅用于 `amap-service serve` 启动校验；API 独立进程，不影响 daemon。
- 敏感项（api_key、mqtt password）沿用双下划线环境变量覆盖。
- `include_geometry` 对两个主题统一生效。

## 4. MQTT 主题与 payload

### 4.1 主题命名（`{prefix}` 默认 `amap`）

| 需求 | 主题 | 内容 |
|---|---|---|
| 需求4 线路地图 | `amap/line/{line_name}/traffic` | 线路逐路段实时路况 |
| 需求5 线路模拟图 | `amap/line/{line_name}/section` | 站间路段占比 + 路况 |

线路双方向放在 payload 内（`directions` 数组）。每轮全量发布即对每条已建线路各发这两个主题各一条。

### 4.2 地图主题 payload（需求4，瘦版默认）

```json
{
  "line_name": "47",
  "traffic_time": "2026-06-09 13:02:00",
  "directions": [
    { "direction": 0,
      "segments": [
        { "seq": 0, "link_id": "5130091959790075998",
          "state": 2, "speed": 18, "travel_time": 35, "reverse": 0 }
      ] }
  ]
}
```
`include_geometry: true` 时每个 segment 追加 `"line_track": "121.47,31.23;121.48,31.24;..."`。

### 4.3 模拟图主题 payload（需求5）

```json
{
  "line_name": "47",
  "traffic_time": "2026-06-09 13:02:00",
  "directions": [
    { "direction": 0,
      "sections": [
        { "from_level_id": 1, "to_level_id": 2,
          "links": [ { "link_id": "5130091959790075998", "state": 2, "pct": 40 } ] }
      ] }
  ]
}
```
每个 section 内 `pct` 之和 = 100；路况缺失默认 `state=1`（沿用 `StationTrafficResolver.default_state`）。`include_geometry: true` 时 link 追加 `line_track`。

### 4.4 关键设计点

1. **`link_id` 一律序列化为字符串**——64 位整数（如 `5130091959790075998` > 2^53），JS 订阅端朴素 JSON 解析会静默损精度。API 响应同理。
2. `traffic_time` 取本轮全量路况时间戳（响应顶层 `utcSeconds` 转东八区），供订阅端判断新鲜度。

## 5. HTTP API 接口

统一前缀 `/api/v1`。响应模型用 pydantic，自动生成 `/docs` OpenAPI 文档。鉴权开启时所有业务接口校验 `X-API-Key`。

| 需求 | 方法 & 路径 | 说明 |
|---|---|---|
| — | `GET /api/v1/health` | 健康检查（不鉴权） |
| — | `GET /api/v1/lines` | 已建线路清单（line_name、方向、站数） |
| 需求3 | `GET /api/v1/lines/{line_name}/segments?direction=` | 线路静态路段信息 |
| 需求4 | `GET /api/v1/lines/{line_name}/traffic?direction=&geometry=false` | 线路逐路段实时路况 |
| 需求5 | `GET /api/v1/lines/{line_name}/sections?direction=` | 整条线路站间占比+路况 |
| 需求5 | `GET /api/v1/lines/{line_name}/sections/{to_level_id}?direction=` | 单个站间区间（`station_section`） |

约定：
- `direction` 省略 = 全部方向；传 `0/1` 只返回该方向。
- **需求3 的 segments 必带 `line_track`**（静态几何就是其用途，不受 `geometry` 参数影响）。
- **需求4/5 的 `geometry` 查询参数默认 `false`**（瘦），`true` 才带 `line_track`——与 MQTT 的 `include_geometry` 对称。
- 需求4/5 响应体与对应 MQTT 主题 payload **字段完全一致**（同一视图层产出）。
- `link_id` 字符串化。

需求3 响应示例：
```json
{
  "line_name": "47",
  "directions": [
    { "direction": 0,
      "segments": [
        { "seq": 0, "link_id": "5130091959790075998",
          "reverse": 0, "line_track": "121.47,31.23;121.48,31.24" }
      ] }
  ]
}
```

错误约定：线路未建/无数据 → `404 {"detail": "line not found: 47"}`；鉴权失败 → `401`；参数非法 → `422`（FastAPI 自动）。

## 6. 性能

针对几百条线 × 双向、每 2 分钟全量发布：

- **路况零回读**：发布器用 traffic 管道内存里的全量 rows 组装，不查 DB、不查 Redis。
- **静态结构内存缓存**：`static_cache` 一次性批量加载所有已建线路的 segment + section_link（2 条 bulk 查询，按 line/dir/seq 排序后内存分组），靠一条 `SELECT max(built_at)` 探针判断是否需重载。线路结构只在 transit-build/section-build 后才变（每天级），平时命中内存。
- **Redis 全量镜像**：每轮全量 rows 经批量 `mset` 写入（复用已有批量接口），TTL 600s。
- **API 侧**：`static_cache` 同款内存缓存（TTL `static_cache_ttl_seconds`，默认 300s）；路况经 `TrafficReader` 命中 Redis 全量镜像，几乎不落 DB。FastAPI async，uvicorn 多 worker 可横向扩。

## 7. 错误处理

核心原则：**发布/接口故障绝不拖垮需求1/2 的数据落地。**

- MQTT 连接失败 / publish 异常 → 发布器捕获并记日志，不抛回 traffic 管道；路况落地照常完成。
- MQTT 单条长连接 + paho 自动重连；`enabled=false` 时 `client.py` 用 NoOp 实现（不 import paho）。
- 某条线路组装视图异常（数据不完整）→ 跳过该线、记日志，继续发其余线路。
- API：线路无数据 404、鉴权 401、参数 422；DB/Redis 异常 → 500 + 日志。
- 全量发布末尾记一行汇总：`published lines=N, map=N, section=N, skipped=N, elapsed=…ms`。

## 8. 测试

沿用现有 pytest 布局：

- `tests/views/` — 视图层组装：瘦/带几何、pct 和=100、缺路况默认 state=1、link_id 字符串化、空线路。
- `tests/publish/` — 发布器：fake MQTT client 断言主题名与 payload；MQTT 异常被吞、不影响管道；NoOp 路径；全量遍历已建线路。
- `tests/api/` — FastAPI `TestClient`：3/4/5 正常、direction 过滤、geometry 开关、404/401/422、与 MQTT payload 同构断言。
- `tests/pipelines/test_traffic.py` 补充：`on_complete` 回调收到**全量** rows（开 incremental 时也是全量，非变更子集）；全量镜像写 Redis。
- 回归：MQTT/API 全关时，traffic 与 road-network 行为与现状逐字节一致。

## 9. CLI 变更

- 新增子命令 `amap-service serve [-c config]`：启动 FastAPI（uvicorn），读 `api` 配置；`api.enabled=false` 时拒绝启动并提示。
- `amap-service run`（daemon）：scheduler 在 `mqtt.enabled=true` 时为 traffic job 注入发布器 `on_complete` 回调。

## 10. 依赖新增

- `fastapi`、`uvicorn[standard]`（API 进程）。
- `paho-mqtt`（发布器；仅 `mqtt.enabled=true` 时实际使用，NoOp 路径不 import）。
- 加入 `pyproject.toml`。

## 11. 不做（YAGNI）

- 不做 MQTT 订阅端/双向通信——只发布。
- 不做 retain/QoS1（按决策 QoS0+retain=false）；如需新订阅者立即取数，后续可加配置项。
- 不做 API 写接口、不做 WebSocket、不做物化缓存（方案 C）——规模不需要。
- 不做按变更增量发布（按决策每轮全发）。
