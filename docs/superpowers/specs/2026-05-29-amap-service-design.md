# 高德地图数据服务 — 设计文档

> 日期：2026-05-29
> 状态：已确认（待用户最终审阅）
> 范围：需求1（地图数据落地）+ 需求2（公交线路加工）+ 需求3（GPS↔路段 SDK），统一一个项目
> 权威需求来源：[demo/note.md](../../../demo/note.md)、[demo/数据字典.md](../../../demo/数据字典.md)

---

## 1. 概述

把高德地图数据获取、清洗、落地为一个配置驱动的 Python 长驻服务，并提供 GPS 轨迹与路段双向转换 SDK。三个交付物共享配置层、数据层与 SDK：

1. **需求1 — 地图数据落地**：按 cron 从「全量路网」「全量实时路况」两个上游接口拉取并落库。
2. **需求2 — 公交线路加工**：token → 线路列表 → 逐条线路对象的串联调用，把线路 GPS 轨迹经 SDK 转成有序路段落库。
3. **需求3 — GPS↔路段 SDK**：`linetrack_to_linkinfos` / `linkinfos_to_tracks` 双向转换，含单车道反向判定。

## 2. 已确认的关键决策

| 维度 | 决策 | 理由 |
|------|------|------|
| 范围 | 三需求统一一个项目（方案 A：模块化单体包） | 需求2 依赖需求3 SDK；共享 config/db/sdk |
| 语言/运行时 | **Python** | 原生大整数，彻底规避 `linkId`（> 2^53）损坏 |
| 运行模型 | **长驻 daemon + 内置 APScheduler**（配置驱动 cron） | 自包含、单进程部署、持有 DB/Redis 连接 |
| 配置格式 | **YAML + pydantic 校验**，敏感项可被环境变量覆盖 | 多接口嵌套结构清晰、强类型校验、容器友好 |
| 实时路况存储 | **upsert，只存最新**（每 link 一行，刷新 `updated_at`） | 以 note.md 为权威（数据字典的「多快照」措辞被覆盖） |
| 分段路况 `listSectionStatus` | **聚合到顶层单行** | 目标表仅建模顶层列；保持 schema 简洁 |
| SDK 匹配 | **DB 驱动空间匹配**（bbox 粗筛 + 点到折线距离） | 贴近真实、可复用；依赖已落地的路网几何 |
| Redis（可选） | 最新路况快照 / 增量检测 / token 缓存（启用才用） | 需求未指定用途，按这三项细分开关 |
| 全量路网刷新 | 按 `link_id` upsert + 坐标**整段替换**（先删后插） | 符合「存在更新不存就插入」；避免坐标脏点残留 |
| 需求2 字段解析与公交表 | **两阶段**：阶段一打通链路 + 存档原始响应；阶段二依真实响应定稿 | 三个公交接口的响应不在日志中，结构未知 |

## 3. 项目结构（方案 A：模块化单体包）

```
AmapService/
├── config/
│   └── config.yaml              # 唯一外置配置
├── amap_service/
│   ├── config/                  # YAML 加载 + 环境变量覆盖 + pydantic 校验
│   │   ├── schema.py
│   │   └── loader.py
│   ├── db/                      # SQLAlchemy Core，方言无关
│   │   ├── engine.py
│   │   ├── schema.py
│   │   ├── migrate.py           # 幂等建表 + 索引
│   │   └── repositories.py      # upsert / 批量写 / 几何只读查询
│   ├── cache/
│   │   └── client.py            # 启用→Redis；未启用→NoOpCache（同接口）
│   ├── clients/                 # 上游 HTTP 封装
│   │   ├── base.py              # 重试/超时/鉴权/流式
│   │   ├── road_network.py
│   │   ├── traffic.py
│   │   └── transit.py
│   ├── parsing/                 # JSON→领域对象（处理数据坑）
│   │   ├── road_network.py
│   │   └── traffic.py
│   ├── pipelines/               # 拉取→解析→落地 编排
│   │   ├── road_network.py
│   │   ├── traffic.py
│   │   └── transit.py
│   ├── sdk/                     # 需求3
│   │   ├── conversion.py        # 对外两方法
│   │   ├── matcher.py           # DB 驱动空间匹配
│   │   └── geometry.py          # 纯函数：距离/方位角/反向判定
│   ├── scheduler/
│   │   └── runner.py            # APScheduler 装配 cron
│   └── cli.py                   # run(daemon) / run-once <job> / initdb
├── tests/
│   └── fixtures/                # 来自真实日志的样本
├── docs/superpowers/specs/
├── pyproject.toml
└── README.md
```

**边界约定（解耦关键）：**
- `sdk/geometry.py` 纯函数、零依赖、最易测。
- `sdk/matcher.py` 只读 `road_link_coord` 几何，不关心数据来源。
- `parsing/` 不碰网络/DB，输入字节/字典、输出领域对象，便于用真实日志片段做单测。
- `cache/` 未启用时返回 `NoOpCache`，业务无需写 `if redis_enabled` 分支。
- `pipelines/` 是唯一把 clients + parsing + db + cache 串起来的层。

## 4. 配置文件设计（需求1.1–1.5 + 需求2 凭据）

`config/config.yaml`，敏感项可被环境变量 `AMAP__SECTION__KEY`（双下划线分层）覆盖。

```yaml
amap:
  endpoint: "http://192.168.102.102:8080"
  auth:
    type: none            # 日志入参为「（无）」；保留 header/token 扩展位
    headers: {}
  jobs:
    road_network:
      path: "/g5_server/map/api/areaLinkPub"
      cron: "0 1 * * *"   # 每天 01:00
      enabled: true
    traffic_status:
      path: "/g5_server/map/api/traffic/status"
      cron: "*/2 * * * *" # 每 2 分钟
      enabled: true

transit:
  enabled: true
  cron: "0 3 * * *"       # 公交加工：note.md 未指定，暂定每天 03:00
  username: "yangs"
  password: "***"         # 建议用环境变量覆盖
  token_url:  "http://203.156.246.118:36000/API/Token/GetToken"
  line_list_url: "http://203.156.246.115:36004/002006/api/BstClientDataService/GetLineFilterNow"
  line_entity_url: "http://203.156.246.115:36006/002001/api/RoadEntityService/GetRoadLineEntity"

database:
  type: sqlite            # sqlite | mysql（默认 sqlite）
  sqlite:
    path: "./road_network.db"
  mysql:
    host: "127.0.0.1"
    port: 3306
    user: "amap"
    password: "***"
    database: "road_network"
    charset: "utf8mb4"

redis:
  enabled: false
  host: "127.0.0.1"
  port: 6379
  db: 0
  password: null
  uses:
    latest_traffic_snapshot: true
    incremental_detection: true
    token_cache: true

http:
  timeout_seconds: 30
  max_retries: 3
  backoff_seconds: 2

sdk:
  match_tolerance_m: 30   # 匹配容差（米）
  reverse_angle_deg: 90   # 方位角夹角 > 此值判反向
  dedup_jitter_m: 5       # 相邻去抖

logging:
  level: INFO
  file: "./logs/amap_service.log"
```

**pydantic 校验要点：**
- cron 在加载时用 `CronTrigger.from_crontab()` 校验，非法表达式**启动即报错**。
- `database.type` 为枚举，仅校验所选方言块。
- `redis.enabled=false` 时连接字段不强校验；启用时校验 host/port。
- 每个 job 带 `enabled` 开关，可单独停某接口。
- 启动时打印生效配置摘要（密码脱敏）。

## 5. 数据层与表结构

### 5.1 已知表（需求1，以数据字典为准）

**`road_link`**：`id`(PK,自增)、`link_id`(BIGINT, UNIQUE)、`road_name`、`length`、`formway`、`roadclass`、`line_track`(TEXT, `"lng,lat;lng,lat"`)、`created_at`。
索引：`link_id` UNIQUE，`road_name`/`formway`/`roadclass` INDEX。

**`road_link_coord`**：`id`(PK)、`link_id`、`seq`(从0)、`longitude`(REAL)、`latitude`(REAL)。
索引：`(link_id, seq)` UNIQUE，`link_id` INDEX。

**`traffic_status`**：`id`(PK)、`link_id`、`speed`、`state`、`travel_time`、`updated_at`。
索引：`link_id`/`state`/`updated_at` INDEX。
**upsert 语义**：按 `link_id` 存在即更新并刷新 `updated_at`，否则插入（每 link 一行最新值）。

### 5.2 落地策略

**全量路网（约 408k links / 911k 坐标，每天一次）：**
- 逐条 upsert `road_link`（存在更新、不存在插入）。
- 坐标子表：对每个更新的 `link_id`，**先删其全部 `road_link_coord` 再按新 `coordList` 重插**（避免新旧点数不一致的脏数据）。
- 批量 + 分批事务（每约 2000 条提交一次），单批失败回滚该批、其余继续。
- 全程**流式解析**（`ijson`）避免一次性载入巨大响应。
- 输出统计：`inserted/updated/skipped/failed`。

**实时路况（每 2 分钟）：**
- 条目带 `listSectionStatus` → 聚合到顶层：行程时间加权平均 `speed`、取最拥堵 `state`、求和 `travelTime`，落一行。
- 按 `link_id` upsert + 刷新 `updated_at`。
- Redis 启用：写「最新路况快照」+ 用上轮哈希做增量检测，跳过未变 link。

**64 位整数：** `link_id` 全程用 Python `int`，SQLite `INTEGER` / MySQL `BIGINT` 原样存取；JSON 解析保留整数精度（不经 float）。

### 5.3 公交表（需求2）— 待真实响应确定（含补全计划）

阶段一即落地、不依赖字段结构：
- **`transit_line_raw`**：`id`、`line_name`、`raw_response`(TEXT/JSON)、`fetched_at` — 原始响应存档，作为字段设计依据与可追溯来源。

阶段二（用户回传响应后）定稿：
- **`transit_segment`**：线路→有序路段，预期含 `line_name`、`seq`、`link_id`、`reverse_coords`、…（具体字段依真实响应）。

### 5.4 方言无关
SQLAlchemy Core 构造 SQL；upsert 按方言适配（SQLite `ON CONFLICT(link_id) DO UPDATE`、MySQL `ON DUPLICATE KEY UPDATE`）。建表/索引在 `initdb` 或启动时幂等执行。

## 6. 上游客户端与流水线

### 6.1 HTTP 客户端基类（`clients/base.py`）
统一超时、指数退避重试、状态码/异常处理、日志；支持流式响应；注入鉴权（地图接口 none，公交走签名/token）。

### 6.2 需求1 流水线
- **路网**：`GET {endpoint}{road_network.path}` → 流式解析 `linkCoordList[*]` → 领域对象（`coordList` 两两成对 `(lng,lat)`、序列化 `line_track`、保留 64bit `linkId`）→ 分批 upsert → 统计。
- **路况**：`GET {endpoint}{traffic_status.path}` → 解析 `linkStates[*]`（分段聚合）→ 按 `link_id` upsert + 刷新 `updated_at` → Redis 可选快照/增量。

### 6.3 需求2 流水线（链路 + 签名 + token 缓存）

**Step 1 · token**（按 note.md .NET 参考精确移植）：
```
ts   = 当前 Unix 毫秒时间戳
sign = MD5("appsecret{password}appkey{username}timestamp{ts}appsecret{password}")  # 小写十六进制
body = "appkey={username}&sign={sign}&timestamp={ts}"   # x-www-form-urlencoded
POST {token_url} body → token
```
token 缓存：Redis 启用且 `token_cache:true` 时缓存（带过期），否则进程内内存缓存。

**Step 2 · 线路列表**：`GET {line_list_url}?loginname={username}`（带 token）。

**Step 3 · 逐线路对象**：对每个线路名 `GET {line_entity_url}?lineName={线路名}`。

**Step 4（阶段二）**：从线路对象取 GPS 轨迹 → `sdk.linetrack_to_linkinfos()` → 写 `transit_segment`。

> **阶段一交付**：完整可跑的三步链路 + 全量原始响应落盘到 `logs/transit_raw/`（真实发起请求，非 mock）。用户回传响应后补 Step 4 与表结构。
> 错误：token 失败中止本轮公交链路；单条线路对象失败跳过该线路、继续其余。

## 7. 需求3 SDK（空间匹配 + 反向判定）

三层：`geometry`（纯函数）→ `matcher`（DB 只读几何）→ `conversion`（对外 API）。

### 7.1 `geometry.py`（纯函数）
- `haversine(p1,p2)` 两点距离（米）
- `point_to_segment_dist(p,a,b)` 点到线段最短距离（打分基础）
- `bearing(a,b)` 方位角（0–360°）
- `is_reverse(track_dir, link_dir)` 方位角夹角 > `reverse_angle_deg`（默认 90°）判反向

### 7.2 `matcher.py`（DB 驱动空间匹配）
- **候选检索**：GPS 点经纬度 bbox 粗筛 `road_link_coord`，缩小候选 link。
- **打分**：候选 link 折线 vs GPS 子轨迹平均/最大 `point_to_segment_dist`，取最小且 ≤ `match_tolerance_m`（默认 30m）。
- **切分**：跨多 link 时沿轨迹归属切分，输出有序、去重、相邻去抖的 link 序列。

### 7.3 `conversion.py`（对外契约）
`LinkInfo = @dataclass(link_id: int, reverse_coords: bool)`

- **`linetrack_to_linkinfos(track: str) -> list[LinkInfo]`**：解析 `"lng,lat;lng,lat;..."` → matcher 出有序 link → 比对存储几何方位角 vs GPS 行进方位角，定 `reverse_coords` → 返回列表。
- **`linkinfos_to_tracks(linkinfos: list[LinkInfo]) -> str`**：各 link 按 `seq` 取坐标，`reverse_coords=True` 则逆序，顺序拼接 + 接缝去重端点 → `"lng,lat;lng,lat;..."`。

### 7.4 执行顺序与边界
- 依赖 `road_link*` 已落地 → **先跑路网落地，再跑公交加工**。
- 空/单点轨迹、无命中、坐标越界 → 返回空列表 + 记日志，不抛异常中断批处理。
- 容差/反向夹角/去抖均可配（`config.yaml` 的 `sdk:` 块）。

## 8. 错误处理与可观测性

- **网络层**：超时 + 指数退避；重试耗尽 → 记录后放弃本轮 job，不影响 daemon 与其他 job。
- **解析层**：单条失败计入 `failed`、跳过、继续；批结束输出统计。
- **DB 层**：分批事务，单批失败回滚该批、后续继续；建表幂等。
- **调度层**：每任务 try/except 包裹；`max_instances=1` + `coalesce=True` 防慢任务自我堆叠。
- **日志**：结构化（文件 + 控制台），每 job 起止打印耗时与统计；启动打印脱敏配置摘要。
- **公交存档**：原始响应写 `logs/transit_raw/`（阶段一关键产物）。

## 9. 测试策略（pytest + TDD）

- **`geometry` 纯函数**：haversine/点到线段/方位角/反向判定，含同点、反向、跨 180° 经度等边界。
- **`parsing` 用真实日志夹具**：从 `amap_api_log.txt` 抽 `linkCoordList`/`linkStates`（含 `listSectionStatus`）样本，断言 64bit `linkId` 不失真、`coordList` 两两成对、`line_track` 序列化、分段聚合正确。
- **`db/repositories`**：临时 sqlite 验证 upsert（插入/更新/坐标整段替换）。
- **`sdk` 集成**：小型已知路网种子库，验证往返一致性（含反向 link 逆序拼接）。
- **`config`**：非法 cron、缺失必填、redis 启用/禁用分支、环境变量覆盖。
- **`cache`**：`NoOpCache` 与真实 Redis 接口一致（fakeredis 或跳过标记）。
- 全程 TDD：每模块先写测试再实现。

## 10. 交付里程碑（实现顺序）

1. 脚手架 + 配置 + DB 建表（`initdb`）
2. 需求3 SDK（geometry → matcher → conversion，纯算法先行）
3. 需求1 路网流水线（流式解析 + upsert）
4. 需求1 路况流水线（分段聚合 + Redis 可选）
5. 调度 daemon + CLI 装配
6. 需求2 阶段一（链路 + 签名 + token 缓存 + 原始响应存档）
7. **〔暂停〕** 用户回传公交响应 → 需求2 阶段二（字段映射 + `transit_segment` + 接 SDK）

## 11. 未决 / 待补（不阻塞阶段一实现）

- **需求2 三接口响应结构**：token/线路列表/线路对象的真实 JSON 字段（用户将在阶段一跑通后回传）。
- **`transit_segment` 最终字段**：依真实响应定稿。
- **公交加工 cron**：暂定每天 03:00，待用户确认。
- **地图接口鉴权**：当前 `auth.type: none`（日志入参为「无」）；若实际需要鉴权头再补。
