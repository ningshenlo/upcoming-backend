# Epic Games Store upcoming channel

## Official source

- Browse page: `https://store.epicgames.com/en-US/browse?category=Game&count=40&sortBy=comingSoon&sortDir=ASC&start=0`
- GraphQL endpoint: `https://store.epicgames.com/graphql`
- Locale/country used by this collector: `en-US` / `US`

The public browse page calls `Catalog.searchStore` with `comingSoon = true`. Direct Python `requests` can be rejected by Epic's Cloudflare layer, so the collector uses `curl_cffi` with `impersonate="chrome"` to preserve a Chrome-like TLS/HTTP2 fingerprint. This avoids running a browser for the current Epic protection mode.

## Discovery request

```http
POST https://store.epicgames.com/graphql
```

Headers:

```http
Accept: application/json
Content-Type: application/json
Origin: https://store.epicgames.com
Referer: https://store.epicgames.com/en-US/browse?category=Game&count=40&sortBy=comingSoon&sortDir=ASC&start=0
```

GraphQL operation: `searchStoreQuery`

Important variables:

```json
{
  "allowCountries": "US",
  "category": "games/edition/base",
  "comingSoon": true,
  "count": 40,
  "country": "US",
  "locale": "en-US",
  "sortBy": "releaseDate",
  "sortDir": "ASC",
  "start": 0,
  "withPrice": true
}
```

Pagination increments `start` by the response `paging.count` until `paging.total` is reached or the configured limit is filled.

## Product request

Tracked refresh uses a single-offer query:

```http
POST https://store.epicgames.com/graphql
```

GraphQL operation: `catalogOfferQuery`

Variables:

```json
{
  "namespace": "<offer namespace>",
  "id": "<offer id>",
  "country": "US",
  "locale": "en-US",
  "withPrice": true
}
```

The collector stores:

- `store_links.sku_id` = Epic offer id
- `store_links.metadata.namespace` = Epic namespace
- `store_links.product_id` = Epic product id when available, otherwise `namespace:offerId`

## Normalized mapping

- `title` -> `games.title`
- `description` -> `games.description`
- `releaseDate` -> `release_events.date`, `launch_time_utc`
- `catalogNs.mappings(pageType: "productHome").pageSlug` -> Epic product page URL
- `catalogNs.mappings(pageType: "productHome").productId` -> `external_ids.epicProductId`, `store_links.product_id`
- `id` -> `external_ids.epicOfferId`, `store_links.sku_id`
- `namespace` -> `external_ids.epicNamespace`, `store_links.metadata.namespace`
- `developerDisplayName` -> developer company
- `publisherDisplayName` -> publisher company
- `price.totalPrice` -> `store_links.price`, `currency`, `price_text`
- `prePurchase` -> `store_links.preorder_available`
- `categories[].path` -> `store_links.metadata.categories`
- `tags[].id` -> `store_links.metadata.tags`
- `keyImages` -> cover/header/screenshot image fields

Platform mapping is fixed for this channel:

- `pc`
- `epic-games-store`

Store link identity:

```text
store_name = "epic_games_store"
store_link.id = "epic_games_store:{productId}"
```

## Active tracking

`epic_tracked_refresh.py` queries existing upcoming games with `store_links.store_name = 'epic_games_store'`.

For each row it:

1. Reads `namespace` from `store_links.metadata.namespace`.
2. Reads offer id from `store_links.sku_id`.
3. Calls `Catalog.catalogOffer(namespace, id)`.
4. Parses the result with the same Epic offer parser.
5. Calls `NeonStore.upsert_collected_game()`.

This reuses the existing write path, so changes are recorded through `game_updates`, `source_observations`, `store_links.last_checked_at`, and the `data_jobs` row with `mode = "epic_tracked_refresh"`.

## Operational notes

Epic's GraphQL endpoint is a website frontend endpoint, not a documented public developer API. If Epic upgrades from passive TLS/HTTP2 fingerprinting to JavaScript challenge or CAPTCHA, the collector may need to switch from `curl_cffi` to a browser-rendering backend.
