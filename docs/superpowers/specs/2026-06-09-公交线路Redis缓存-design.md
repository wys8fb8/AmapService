# 公交线路对象 Redis 缓存设计

日期：2026-06-09
状态：已评审通过，直接 TDD 实现（改动小）

## 背景与目标

`run_transit_build` 每次运行都重新拉取上游：token（已缓存）→ 线路列表（未缓存）→ 逐条 `GetRoadLineEntity`（未缓存）。逐线路对象走 3 跳慢上游，是最耗时部分；而线路对象每天最多变一次。

目标：把线路列表与线路对象缓存到 Redis 一份，**当日重复用（调试、重建）不再重复打上游**，每天自动刷新。

## 决策

- 方案 A：在 `TransitClient` 内缓存（对 build/stage1 透明）。
- 失效：动态 TTL = 从现在到下一个 `expire_hour:00`（UTC+8，默认 01:00）的秒数 → 当日命中，跨天 1 小时后失效。
- key 不带日期（靠 TTL 失效，避免午夜立刻 miss）。
- 仅缓存成功响应（`status < 300`），错误响应不缓存（沿用 token "成功才缓存"）。
- 开关复用 `redis.uses`，新增 `transit_line_cache`（默认 true），仅 `redis.enabled=true` 时生效。

## 实现

### 配置（`amap_service/config/schema.py`）
- `RedisUses.transit_line_cache: bool = True`
- `TransitConfig.line_cache_expire_hour: int = 1`（缓存失效的整点小时，UTC+8）

### TransitClient（`amap_service/clients/transit.py`）
- 构造新增：`line_cache_enabled: bool = False`、`line_cache_expire_hour: int = 1`。
- 模块级辅助：`seconds_until_next_local_hour(now_ms, hour, tz_offset_hours=8) -> int`，返回到下一个 `hour:00`（指定时区）的秒数（≥1）。
- `_line_cache_enabled()`：`line_cache_enabled and cache 存在 and cache.enabled`。
- `get_line_list(token)`：启用则先查 `transit:line_list`；命中返回缓存文本（不发请求）；未命中拉取，`status < 300` 时按 TTL 回写。
- `get_line_entity(token, line)`：key `transit:line_entity:{line}`；逻辑同上。
- `get_token` 不变。

### CLI（`amap_service/cli.py`）
构造 `TransitClient` 时补传 `line_cache_enabled=config.redis.uses.transit_line_cache`、`line_cache_expire_hour=config.transit.line_cache_expire_hour`。

### 配置样例
`config/config.yaml.example`（及本地 `config/config.yaml`）：`redis.uses` 加 `transit_line_cache: true`；`transit` 加 `line_cache_expire_hour: 1`。

## 权衡
stage1 与 build 共用 client，启用后 stage1 当日也读缓存归档（仍是当日数据，只是不重复抓）。可接受；如需 stage1 永远抓新，再加路径级开关（YAGNI，暂不做）。

## 测试
- 线路列表/对象 Redis 缓存命中：第二个 client 用同一 fakeredis + 启用 → 返回缓存、不再请求。
- 仅缓存成功：500 不缓存，下次仍请求。
- 禁用时始终请求。
- TTL 辅助函数：固定 now_ms，断言到下一个 01:00 的秒数；并经 fakeredis 验证 key 的 TTL 已设置。
- 回归：现有 transit client 测试全绿。
