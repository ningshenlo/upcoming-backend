# PS5即将发行游戏数据获取方法

> 更新时间：2026-06-05

## 1. 目标页面判断

用户提供的页面是 PlayStation 官网 PS5 游戏页：

```text
https://www.playstation.com/en-us/ps5/games/?smcid=pdc%3Aen-us%3Aps5%3Aprimary%20nav%3Amsg-ps5%3Agames#pre-order
```

其中 `#pre-order` 只是浏览器端锚点，不会参与服务端请求。该页面本身更像 PlayStation 官网的营销/推荐页，页面里有 “Coming soon” / “Pre-order and Wishlist upcoming PS5 games” 区块，但它不是完整的即将发行游戏数据库。

结论：

- 可以解析该页面作为展示参考。
- 不建议把该页面作为主采集源。
- 更适合作为主数据源的是 PlayStation Store 使用的 GraphQL 接口。

## 2. 更适合采集的 PlayStation Store 来源

PlayStation Store 有官方商店分类页：

```text
https://store.playstation.com/en-us/category/82ced94c-ed3f-4d81-9b50-4d4cf1da170b
```

该分类对应 “Coming soon” 游戏列表，更接近我们需要的 PS5 upcoming games 数据源。

页面背后使用 GraphQL：

```text
https://web.np.playstation.com/api/graphql/v1/op
```

该接口不是对外文档化的公开 API，而是 PlayStation Store 前端正在使用的接口。稳定性强于直接解析营销页，但仍需要考虑 persisted query hash 变化。

## 3. 拉取列表：categoryGridRetrieve

用于拉取 Coming soon 分类列表的 operation：

```text
categoryGridRetrieve
```

当前验证可用的 persisted query hash：

```text
9845afc0dbaab4965f6563fffc703f588c8e76792000e8610843b8d3ee9c4c09
```

核心变量：

```json
{
  "id": "82ced94c-ed3f-4d81-9b50-4d4cf1da170b",
  "pageArgs": {
    "size": 24,
    "offset": 0
  },
  "sortBy": null,
  "filterBy": [],
  "facetOptions": []
}
```

返回结构里重点关注：

- `categoryGridRetrieve.concepts`
- `categoryGridRetrieve.pageInfo.totalCount`
- `categoryGridRetrieve.pageInfo.isLast`
- `categoryGridRetrieve.facetOptions`

`concepts` 列表里通常能拿到：

- `id`，即 PlayStation concept id
- `name`
- `media`
- `products`

列表接口通常不直接返回完整发行日期，因此需要继续请求详情。

## 4. 拉取详情：metGetConceptById

用于按 concept id 拉取游戏详情的 operation：

```text
metGetConceptById
```

当前验证可用的 persisted query hash：

```text
cc90404ac049d935afbd9968aef523da2b6723abfb9d586e5f77ebf7c5289006
```

核心变量：

```json
{
  "conceptId": "10002861",
  "productId": ""
}
```

详情接口重点字段：

- `conceptRetrieve.id`
- `conceptRetrieve.name`
- `conceptRetrieve.releaseDate`
- `conceptRetrieve.defaultProduct.id`
- `conceptRetrieve.defaultProduct.platforms`
- `conceptRetrieve.defaultProduct.releaseDate`
- `conceptRetrieve.defaultProduct.type`
- `conceptRetrieve.defaultProduct.subType`
- `conceptRetrieve.publisherName`
- `conceptRetrieve.genres`
- `conceptRetrieve.media`
- `conceptRetrieve.contentRating`

已验证示例：

- `Marvel's Wolverine`
- concept id：`10002861`
- 默认产品平台：`["PS5"]`
- 类型：`GAME`
- 子类型：`FULL_GAME`
- 发行时间字段：`releaseDate`

## 5. 请求头要求

请求 GraphQL 时需要带必要请求头，否则可能触发 Apollo CSRF 防护，或者返回非 US 区域数据。

建议固定：

```text
Content-Type: application/json
x-apollo-operation-name: categoryGridRetrieve 或 metGetConceptById
Accept-Language: en-US,en;q=0.9
Origin: https://store.playstation.com
Referer: https://store.playstation.com/en-us/
```

区域头很重要。未固定区域时，可能拿到英国/欧洲区结果，例如 PEGI 分级、英镑价格、不同 UTC 时间。

## 6. 另一个可选分类：产品级 Coming soon

另一个 PlayStation Store Coming soon 分类页：

```text
https://store.playstation.com/en-us/category/a00d4d61-f6bc-4a00-bb68-ff0bb43fcc33
```

该分类返回更偏产品级数据，适合补充预购产品、价格、SKU、版本信息。

可用过滤方式：

```json
{
  "id": "a00d4d61-f6bc-4a00-bb68-ff0bb43fcc33",
  "pageArgs": {
    "size": 24,
    "offset": 0
  },
  "sortBy": {
    "name": "productReleaseDate",
    "isAscending": true
  },
  "filterBy": [
    "storeDisplayClassification:FULL_GAME"
  ],
  "facetOptions": []
}
```

这个分类的特点：

- 会返回 `products`，而不是 `concepts`。
- 更容易拿到价格、SKU、预购状态。
- 可能出现同一个游戏的多个版本，需要按 concept/product 关系去重。
- 可能混入 bundle、edition、add-on，需要过滤。

MVP 阶段不建议优先使用它作为主列表源，更适合作为补充数据源。

## 7. 本项目建议方案

MVP 阶段建议：

1. 使用 `categoryGridRetrieve` 请求 `82ced94c-ed3f-4d81-9b50-4d4cf1da170b` 分类。
2. 按 `pageInfo.isLast` 或 `totalCount` 分页抓取全部 `concepts`。
3. 对每个 concept id 请求 `metGetConceptById`。
4. 过滤 `defaultProduct.platforms` 包含 `PS5` 的游戏。
5. 使用 concept id 作为 PlayStation 侧主去重键。
6. 保存 product id / npTitleId 作为外部平台补充标识。
7. 固定 US 区域请求头，统一日期、分级、价格语境。
8. 对 persisted query hash 做配置化管理，失效时报警。

当前不建议：

- 直接把 `playstation.com/en-us/ps5/games/#pre-order` 当完整数据源。
- 只解析 HTML 文案来判断发行日期。
- 不区分 concept 和 product，直接按标题去重。
- 高频轮询 GraphQL。

## 8. 风险点

- GraphQL persisted query hash 可能变化，需要从浏览器 Network 里重新获取。
- PlayStation Store 分类内容可能按地区不同而变化。
- `releaseDate` 是时间戳，展示为日期时需要结合目标地区处理。
- 产品级分类可能包含豪华版、捆绑包、DLC、预购包，需要额外过滤。
- 该接口是商店前端接口，不是正式公开承诺稳定的开发者 API。

## 9. 参考来源

- PlayStation PS5 games 页面：https://www.playstation.com/en-us/ps5/games/
- PlayStation Store Coming soon 分类：https://store.playstation.com/en-us/category/82ced94c-ed3f-4d81-9b50-4d4cf1da170b
- PlayStation Store 产品级 Coming soon 分类：https://store.playstation.com/en-us/category/a00d4d61-f6bc-4a00-bb68-ff0bb43fcc33
- playstation-store-api 项目说明：https://github.com/mrt1m/playstation-store-api
