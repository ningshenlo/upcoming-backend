# Game updates change tracking

## 变更背景

当前采集链路已经能把 Steam 等官方来源的数据写入 `games`、`release_events`、`store_links` 和 `source_observations`，但过去的行为主要是 upsert 覆盖：数据变了以后，主表会更新，证据表会新增观察记录，产品侧却没有一张清晰的“最近变化”时间线。

本次新增 `game_updates`，用于把关键字段变化沉淀成可展示、可订阅、可回溯的产品级更新记录。

## 新增表

迁移文件：`migrations/20260609_game_updates.sql`

新增表：`game_updates`

核心字段：

- `game_id`：关联游戏。
- `store_link_id`：可选，关联触发变化的商店链接。
- `source_slug` / `source_url`：变化来源。
- `update_type`：变化类型。
- `title` / `summary`：前台可直接展示的更新标题和摘要。
- `before_value` / `after_value`：变化前后的结构化值。
- `raw_data_path` / `data_job_id`：关联采集原始数据和任务。
- `dedupe_key`：同一变化去重，避免重复同步产生重复动态。

当前支持的 `update_type`：

- `release_date_announced`
- `release_date_changed`
- `release_date_confirmed`
- `demo_available`
- `demo_removed`
- `price_available`
- `price_changed`
- `metadata_enriched`
- `metadata_changed`
- `company_changed`

## 写入流程

入口仍然是 `NeonStore.upsert_collected_game()`。

流程：

1. 入库前按当前游戏 slug 读取旧快照。
2. 正常执行现有 `games`、`release_events`、`game_companies`、`store_links` upsert。
3. 入库后对比旧快照和本次 `CollectedGame`。
4. 如果关键字段发生变化，写入 `game_updates`。
5. 如果 `game_updates` 表尚未迁移，代码会跳过写入，不影响原有同步。

## 主动追踪任务

新增脚本：`steam_tracked_refresh.py`
新增脚本：`playstation_tracked_refresh.py`

它们和普通发现型 collector 的分工不同：

- `official_release_sync.py --collectors steam`：从 Steam coming soon 搜索页发现和同步一批游戏。
- `steam_tracked_refresh.py`：从数据库读取已经收录的 upcoming Steam app id，逐个请求 Steam appdetails 和商店页，刷新旧游戏。
- `official_release_sync.py --collectors playstation`：从 PlayStation Store coming soon 分类发现和同步一批游戏。
- `playstation_tracked_refresh.py`：从数据库读取已经收录的 upcoming PlayStation concept id，逐个请求 PlayStation concept detail 和商店页，刷新旧游戏。
- `official_release_sync.py --collectors xbox`：从 Xbox Games Coming Soon 官方页和 Emerald 前端服务发现和同步一批游戏。
- `xbox_tracked_refresh.py`：从数据库读取已经收录的 upcoming Xbox product id，逐个请求 Xbox product endpoint，刷新旧游戏。

主动追踪脚本会复用对应渠道的 parser 和 `NeonStore.upsert_collected_game()`，因此会自然触发 `game_updates` 差异检测。

建议调度：

- `official_release_sync`：每天 1-2 次，用于发现新游戏。
- `steam_tracked_refresh`：每 6 小时一次，用于主动追踪已收录游戏变化。
- `playstation_tracked_refresh`：每 6 小时一次，用于主动追踪已收录 PlayStation 游戏变化。
- `xbox_tracked_refresh`：每 6 小时一次，用于主动追踪已收录 Xbox 游戏变化。
- `steam_metadata_backfill`：每天 1 次或按需运行，用于补齐缺 metadata 的旧数据。

## 当前追踪范围

当前只追踪官方采集结果里已经有的高价值变化：

- 发售日期从无到有。
- 发售日期发生变化。
- 发售日期精度提升，例如 `year` 到 `exact`。
- Steam demo 从无到有，或从有到无。
- Steam 价格从未知到已知，或价格信息变化。
- Steam 标签、genres、categories、languages、中文支持信息补全或变化。
- publisher / developer 信息变化。

## 边界

这次没有新增新闻源采集，也没有做媒体文章理解。也就是说，系统现在能追踪“官方数据变化”，但还不会主动读取 GamesRadar、官网新闻、Steam 新闻页、YouTube 等外部内容并抽取事件。

后续如果要做完整的游戏动态追踪，可以在新的新闻/公告采集器中复用 `game_updates`，把文章抽取后的事件也写入同一张时间线表。
