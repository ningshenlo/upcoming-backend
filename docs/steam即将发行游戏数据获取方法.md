# steam即将发行游戏数据获取方法

> 更新时间：2026-06-05

## 1. 当前可用的 Steam 官方商店入口

当前 `data-scraper/collectors/steam.py` 使用的是 Steam Store 搜索页的 AJAX 结果接口：

```text
https://store.steampowered.com/search/results/
  ?query
  &start=0
  &count={limit}
  &filter=comingsoon
  &infinite=1
  &force_infinite=1
```

该接口返回 JSON，其中核心字段是 `results_html`。当前采集器从 HTML 片段中解析：

- Steam App ID
- 游戏标题
- 商店链接
- 展示发行日期
- 图片 URL

它是 Steam 官方商店页面使用的数据入口，但不是 Steamworks 正式文档中承诺稳定的 WebAPI。

## 2. 推荐构造参数

用于采集即将发行游戏时，建议构造成分页请求：

```text
https://store.steampowered.com/search/results/
  ?query
  &start={offset}
  &count=50
  &filter=comingsoon
  &infinite=1
  &force_infinite=1
  &sort_by=Released_ASC
  &category1=998
  &cc=US
  &l=english
```

参数说明：

- `start`：分页偏移量，例如 `0, 50, 100...`
- `count=50`：每页数量，建议不要过大
- `filter=comingsoon`：即将发行过滤
- `sort_by=Released_ASC`：按发行时间升序，优先拿最近即将发行
- `category1=998`：尽量限制为游戏，减少 DLC、软件、Soundtrack 干扰
- `cc=US&l=english`：固定地区和语言，方便日期解析

采集方式建议：

- 定时分页抓取，而不是高频轮询
- 每页请求之间保留限速间隔
- 用 `steamAppId` 去重，不用标题去重
- 对日期为空、`Coming Soon`、月份、季度、年份、完整日期都做容错

## 3. Steam 官方 WebAPI 的作用

Steam 确实有官方 WebAPI，但目前没有确认存在一个可直接返回完整「即将发行游戏列表 + 发行日期」的官方接口。

官方可用的相关接口是 `IStoreService/GetAppList`：

```text
https://partner.steam-api.com/IStoreService/GetAppList/v1/
```

它的用途是获取 Steam Store 上的 app 列表，并支持：

- `include_games`
- `include_dlc`
- `include_software`
- `last_appid`
- `max_results`
- `if_modified_since`

适合用于发现和增量同步 Steam App ID，但它不等同于「upcoming games API」。

旧接口 `ISteamApps/GetAppList/v2` 已被官方标注为 deprecated，官方建议改用 `IStoreService/GetAppList`。

## 4. appdetails 的作用

Storefront `appdetails` 可用于按 App ID 补充详情：

```text
https://store.steampowered.com/api/appdetails?appids={appid}&cc=US&l=english
```

它通常可以返回：

- `type`
- `name`
- `steam_appid`
- `release_date.coming_soon`
- `release_date.date`
- `header_image`
- `platforms`
- `genres`
- `categories`

建议用途：

- 对 `search/results` 抓到的候选 App ID 做二次校验
- 过滤非游戏、DLC、Demo、Soundtrack
- 补充封面、平台、类型等字段

注意：`appdetails` 是 Steam Storefront 常用接口，但不是 Steamworks 官方文档中完整承诺稳定的接口，需要限速和失败兜底。

## 5. SteamDB 的数据大概率怎么来

SteamDB 不提供公开 API，也明确不允许自动抓取 SteamDB。

根据 SteamDB FAQ，它的数据来源大致包括：

- 使用 SteamKit 接入 Steam network
- 依赖 Steam 自身 app/package 更新系统
- 获取 app/package 基础信息
- 普通 Steam 账号可通过 Steam 控制台命令 `app_info_print {appid}` 查看部分 appinfo
- 可用 SteamCMD 自动化获取部分信息
- 多数 Store 信息仍会解析 Steam store pages，因为不是所有信息都存在于 API 中

因此，SteamDB 的 `/upcoming/` 更可能是基于它长期维护的 appinfo/store 数据库生成的视图，而不是单独调用一个公开的 upcoming API。

## 6. 本项目建议方案

MVP 阶段建议采用低复杂度组合：

1. 用 `store.steampowered.com/search/results/?filter=comingsoon...` 作为 Steam 即将发行主列表来源。
2. 使用 `start` 分页抓取，固定 `cc=US&l=english`。
3. 解析 `results_html`，获得 `steamAppId`、标题、链接、展示日期、图片。
4. 可选：对每个 `steamAppId` 请求 `appdetails` 做二次校验和补充字段。
5. 后续如果需要更完整的全量发现，再接入官方 `IStoreService/GetAppList` 做 appid 增量发现。

当前不建议：

- 抓取 SteamDB
- 依赖 SteamDB 作为数据源
- 直接把 SteamKit/SteamCMD 纳入 MVP，除非后续需要接近 SteamDB 的完整 appinfo 追踪能力

## 7. 参考来源

- Steam upcoming 搜索入口：https://store.steampowered.com/search/?filter=comingsoon
- Steam upcoming 页面：https://store.steampowered.com/explore/upcoming/
- Steam WebAPI 概览：https://partner.steamgames.com/doc/webapi_overview
- IStoreService/GetAppList：https://partner.steamgames.com/doc/webapi/IStoreService
- ISteamApps/GetAppList deprecated 说明：https://partner.steamgames.com/doc/webapi/ISteamApps
- SteamDB FAQ：https://steamdb.info/faq/

## 8. 2026-06-08 实测补充：价格、分类、用户标签的数据来源

本次用 Chrome 实测 Steam app 页面，并用 HTTP 客户端请求同一批商店接口后，确认 Steam 商店数据应分三层采集。

### 8.1 发现列表：`search/results`

```text
https://store.steampowered.com/search/results/
  ?query=
  &start=0
  &count=50
  &filter=comingsoon
  &infinite=1
  &force_infinite=1
  &sort_by=Released_ASC
  &category1=998
  &cc=US
  &l=english
```

用途：

- 发现 upcoming app id。
- 获取标题、商店 URL、发售日期文本、capsule image。
- 搜索行 HTML 里可能有价格块：`data-price-final`、`discount_final_price`、`search_price`。
- 搜索行也有 `data-ds-tagids`，但只有 tag id，不直接给 tag 名称。

限制：

- 只适合作为发现入口。
- 不够稳定地提供 price/category/tag 的完整名称。
- 列表会混入 demo、soundtrack、beta weekend 等，需要 appdetails 再确认类型。

### 8.2 基础详情：`api/appdetails`

```text
https://store.steampowered.com/api/appdetails?appids={appid}&cc=US&l=english
```

用途：

- `type`：过滤 `music`、`dlc` 等非游戏内容。
- `release_date`：确认 coming soon 与日期文本。
- `price_overview`：已定价或已发售条目有真实价格。
- `is_free`：Free To Play / demo 可作为真实价格状态。
- `genres`：Steam 官方 genre。
- `categories`：Steam 功能/玩法/能力标签，例如 `Single-player`、`Co-op`、`Game demo`。
- `developers` / `publishers`。
- `supported_languages`。

限制：

- 很多 coming soon 游戏还没有公开价格，因此 `price_overview` 为空是正常情况，不应伪造价格。
- `genres/categories` 不是 Steam 页面上的 “Popular user-defined tags”。

### 8.3 用户标签：app page HTML

```text
https://store.steampowered.com/app/{appid}/?cc=US&l=english
```

用途：

- 页面 HTML 中的 `.app_tag` 是 Steam 页面展示的用户标签名称。
- 例如 `Blades of Four` 页面可拿到 `Action`、`Roguelike`、`Survival`、`Action Roguelike`、`Top-Down`。

采集策略：

- 对 search/appdetails 得到的 app id 再请求 app page。
- 解析 `.app_tag`，过滤 `+`。
- 写入 `store_links.metadata.steamTags`。
- 同步写入 `metadata.tags`，作为前台卡片的优先展示标签。

### 8.4 当前项目推荐链路

1. `search/results` 做 upcoming app 发现。
2. `appdetails` 做类型过滤、价格、genre、category、publisher/developer、语言补充。
3. `app page HTML` 做 Steam 用户标签补充。
4. 对已有库中缺 price/category/tag 的 Steam app id，使用 `data-scraper/steam_metadata_backfill.py` 定期回填。
5. 前台 Steam 列表只展示至少具备真实价格、category、genre 或 user tag 的卡片，避免 `Categories unknown` 这种低质量占位进入列表。
