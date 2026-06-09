# Xbox upcoming channel

## Official source

- Page: `https://www.xbox.com/en-US/games/browse/DynamicChannel.GamesComingSoon`
- Frontend service: `https://emerald.xboxservices.com/xboxcomfd`
- Channel id: `DynamicChannel.GamesComingSoon`
- Channel key: `BROWSE_CHANNELID=DYNAMICCHANNEL.GAMESCOMINGSOON_FILTERS=`

The page injects `window.__PRELOADED_STATE__`. The collector reads it to get:

- `appContext.marketInfo.locale`
- `appContext.telemetryInfo.initialCv`
- `core2.channels.channelData`

The `initialCv` value is required as the `MS-CV` request header. Without it the Emerald service rejects the request.

## Discovery request

`collectors/xbox.py` posts to:

```http
POST https://emerald.xboxservices.com/xboxcomfd/browse?locale=en-US
```

Headers:

```http
Origin: https://www.xbox.com
Referer: https://www.xbox.com/en-US/games/browse/DynamicChannel.GamesComingSoon
X-MS-API-Version: 1.1
MS-CV: <initialCv from window.__PRELOADED_STATE__>
```

Initial body:

```json
{
  "Filters": "",
  "ReturnFilters": true,
  "ChannelKeyToBeUsedInResponse": "BROWSE_CHANNELID=DYNAMICCHANNEL.GAMESCOMINGSOON_FILTERS=",
  "ChannelId": "DynamicChannel.GamesComingSoon"
}
```

Pagination uses the previous response `encodedCT`:

```json
{
  "Filters": "",
  "ReturnFilters": false,
  "ChannelKeyToBeUsedInResponse": "BROWSE_CHANNELID=DYNAMICCHANNEL.GAMESCOMINGSOON_FILTERS=",
  "ChannelId": "DynamicChannel.GamesComingSoon",
  "EncodedCT": "<previous encodedCT>"
}
```

## Product request

Tracked refresh uses the product batch endpoint:

```http
POST https://emerald.xboxservices.com/xboxcomfd/products?locale=en-US
```

Headers:

```http
Origin: https://www.xbox.com
Referer: https://www.xbox.com/en-US/games/browse/DynamicChannel.GamesComingSoon
X-MS-API-Version: 1.0
MS-CV: <initialCv from window.__PRELOADED_STATE__>
```

Body:

```json
{
  "productIds": ["9PJH4HDZVXHD"]
}
```

## Normalized mapping

- `productSummaries[].productId` -> `external_ids.xboxProductId`, `store_links.product_id`
- `productSummaries[].title` -> `games.title`
- `productSummaries[].releaseDate` -> `release_events.date`
- `productSummaries[].availableOn` -> platform slugs
- `productSummaries[].categories` -> `store_links.metadata.categories`, `genres`, `tags`
- `productSummaries[].developerName` -> developer company
- `productSummaries[].publisherName` -> publisher company
- `images.boxArt` -> `cover_image_url`
- `images.superHeroArt` -> `header_image_url`
- `availabilitySummaries[].price` -> `store_links.price`, `currency`, `price_text`
- `skuSummaries[].isPreorder` -> `store_links.preorder_available`

Platform mapping:

- `XboxSeriesX` -> `xbox-series`
- `XboxOne` -> `xbox-one`
- `PC` -> `pc`

Store link identity:

```text
store_name = "xbox_store"
store_link.id = "xbox_store:{productId}"
```

## Active tracking

`xbox_tracked_refresh.py` queries existing upcoming games with `store_links.store_name = 'xbox_store'`.

For each row it:

1. Reuses a fresh `MS-CV` parsed from the official Xbox page.
2. Calls the product batch endpoint with the stored `product_id`.
3. Parses the response with the same Xbox product parser.
4. Calls `NeonStore.upsert_collected_game()`.

This reuses the existing write path, so changes are recorded through `game_updates`, `source_observations`, `store_links.last_checked_at`, and the `data_jobs` row with `mode = "xbox_tracked_refresh"`.

## Operational notes

The Emerald endpoint is an Xbox website frontend service, not a documented public API. The collector should keep using the official page to obtain `MS-CV` on each run.
