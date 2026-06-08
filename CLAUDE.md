# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目现状

这是一个**全新（greenfield）项目**：目前仅包含需求、参考文档和一份抓取的 API 日志。**尚无任何源代码、构建系统、包管理清单或测试**，因此也没有可运行的 build/lint/test 命令。第一个实现任务是选定技术栈并按 [demo/note.md](demo/note.md) 构建服务。不要臆造不存在的命令；先引入真实的工具链，再来记录它。

所有工作文件都位于 [demo/](demo/) 目录下：

| 文件 | 作用 |
|------|------|
| `demo/note.md` | 权威的**需求规格说明**（3 个需求，中文）。从这里开始读。 |
| `demo/数据字典.md` | 由 API 响应整理出的目标 SQLite 表结构（数据字典）。 |
| `demo/地图数据接口说明V1.0.docx` | 高德原始接口文档（二进制；上面两个 `.md` 文件均由它衍生而来）。 |
| `demo/amap_api_log.txt` | 约 860 万行、约 244MB 的真实高德 API 请求/响应抓包。响应结构的事实来源。**切勿整体读取**——会撑爆上下文。请用 `grep -an` / `awk 'NR>=A && NR<=B'` 提取片段。 |

注意：`note.md` 中把日志称为 `api_log.txt`，但实际文件名是 `amap_api_log.txt`。

## 要构建什么

三个交付物（完整规格见 `demo/note.md`）：

1. **高德地图数据落地服务** —— 配置驱动的拉取器，按 cron 计划从两个上游接口拉取数据并写入数据库。
2. **公交线路数据加工** —— 获取 token + 线路列表 + 线路对象 的流水线（不同主机），把每条线路的 GPS 轨迹转换成有序的路段记录。
3. **GPS 轨迹 ↔ 路段 SDK** —— 双向转换库，并带反向（逆行）判定。

### 配置文件是硬性要求（需求 1）

配置文件必须外置：接口 endpoint 基础地址 + 两个地图数据路径；每个接口的拉取频率以 **cron 表达式**表示；认证信息；同时支持 **MySQL 和 SQLite、默认 SQLite** 的数据库连接；以及一个**可选的 Redis** 配置块，带启用开关（仅在启用时才使用 Redis）。

规格中的默认计划：全量路网 = **每天 01:00**；全量实时路况 = **每 2 分钟**。这些间隔必须由配置驱动，不可硬编码。

## 上游接口（日志中的基础主机：`http://192.168.102.102:8080`）

地图数据（需求 1）：
- **全量路网** —— `GET /g5_server/map/api/areaLinkPub`（日志接口名：`道路全量`）
- **全量实时路况** —— `GET /g5_server/map/api/traffic/status`（日志接口名：`交通路况-实时全量`）

公交线路（需求 2 —— 不同主机/端口，按顺序串联调用）：
1. `POST http://203.156.246.118:36000/API/Token/GetToken` —— 获取 token。签名 = `MD5("appsecret{密码}appkey{用户名}timestamp{ts}appsecret{密码}")`，`ts` = Unix 纪元起的毫秒数；POST 体为 `appkey={用户名}&sign={签名}&timestamp={ts}`。`note.md` 中附有 .NET 参考代码片段。
2. `GET http://203.156.246.115:36004/002006/api/BstClientDataService/GetLineFilterNow?loginname=...` —— 线路列表。
3. `GET http://203.156.246.115:36006/002001/api/RoadEntityService/GetRoadLineEntity?lineName=...` —— 单条线路对象（在线路列表上循环调用）。

## 数据模型与响应结构的坑

`数据字典.md` 中的表结构（`road_link`、`road_link_coord`、`traffic_status`）是落地的目标。**原始 API JSON** 与 **目标表结构** 之间的关键差异——这些正是数据落地的真正工作量，且极易出错：

- **`coordList` 是扁平的数字数组**，不是坐标对象：`[经度, 纬度, 经度, 纬度, ...]`。必须两两成对解析为 `road_link_coord(seq, longitude, latitude)`，同时还要序列化成冗余的 `road_link.line_track` 字符串（`经度,纬度;经度,纬度`）。注意顺序是 **经度,纬度**（高德约定）。
- **`linkId` 超过 2^53**（例如 `5130091959790075998`）。它是 64 位整数，必须按 64 位保留。JavaScript/Node 的朴素 JSON 解析会**静默损坏**这些值；请使用支持 bigint 的解析器，或使用原生支持 64 位整数的语言/栈。SQLite 的 `INTEGER` 可正常存放。
- **实时路况可能分段**：`linkStates` 中的条目除了顶层字段外，还可能带一个 `listSectionStatus` 数组（按 offset 给出的 `speed`/`state`/`travelTime`/`reliability`）。目标表 `traffic_status` 只建模了顶层值——落地时需决定如何处理分段路段。
- **按 `link_id` upsert**：路网——`link_id` 存在则更新，否则插入。路况——按 `link_id` upsert 并刷新 `updated_at`（数据字典文字中还描述了追加时间快照行；实现前请与 `note.md` 的“存在即更新”表述对齐）。

`formway`、`roadclass`（路网）和 `state`（路况）的枚举含义已在 `数据字典.md` 中列表给出——直接使用，不要重新推导。

## SDK 契约（需求 3）

- `linetrack_to_linkinfos(track)` —— 入参为 `"经度,纬度;经度,纬度;..."`，返回 `{ link_id, reverse_coords }` 列表。对于单车道（无通行方向）的路段，当 GPS 轨迹方向与存储的路段几何相反时，置位 `reverse_coords`。
- `linkinfos_to_tracks(linkinfos)` —— 逆向操作；返回拼接后的 `"经度,纬度;经度,纬度"` 字符串，并遵循 `reverse_coords` 标志。

## 处理 API 日志

```bash
# 查找某接口/接口名出现的位置
grep -an "areaLinkPub" demo/amap_api_log.txt | head
grep -an "^接口：" demo/amap_api_log.txt | sort | uniq -c   # 接口名频次（在整文件上较慢）

# 读取指定片段（记录以一行 '=' 分隔，块结构为 接口：/请求：/--- 入参 ---/--- 出参 ---）
awk 'NR>=299588 && NR<=299660' demo/amap_api_log.txt
```
