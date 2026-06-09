# GOG upcoming channel

## Official source

- Browse page: `https://www.gog.com/en/games?releaseStatuses=upcoming&order=asc:releaseDate`
- Catalog endpoint: `https://catalog.gog.com/v1/catalog`
- Detail endpoint: `https://api.gog.com/v2/games/{product_id}`
- Locale/country/currency used by this collector: `en-US` / `US` / `USD`

The GOG website uses the catalog endpoint for store browsing. This is an official GOG frontend dependency, not a documented public partner API.

## Discovery request

```http
GET https://catalog.gog.com/v1/catalog
```

Important query params:

```text
limit=48
page=1
order=asc:releaseDate
productType=in:game
releaseStatuses=in:upcoming
countryCode=US
locale=en-US
currencyCode=USD
```

Pagination increments `page` until the response `pages` value is reached or the configured limit is filled.

## Product request

Tracked refresh uses:

```http
GET https://api.gog.com/v2/games/{product_id}?locale=en-US&countryCode=US&currencyCode=USD
```

This endpoint provides the current release status, product title, description, developer, publisher, tags, features, screenshots, videos, and supported operating systems.

## Normalized mapping

- `id` -> `external_ids.gogProductId`, `store_links.product_id`
- `slug` -> `external_ids.gogSlug`, `store_links.sku_id`
- `title` -> `games.title`
- `storeLink` / `_links.store.href` -> `source_url`, `store_links.url`
- `releaseDate` / `globalReleaseDate` -> `release_events.date` only when the date is today or in the future
- `coverVertical` / `boxArtImage` -> cover image
- `coverHorizontal` / `galaxyBackgroundImage` -> header image
- `screenshots` -> screenshot URLs
- `genres`, `tags`, `features`, `operatingSystems` -> `store_links.metadata`
- `developers`, `publishers` -> company names
- `price.finalMoney` -> `store_links.price`, `currency`, `price_text`

Platform mapping is fixed for this channel:

- `pc`
- `gog`

Store link identity:

```text
store_name = "gog"
store_link.id = "gog:{productId}"
```

## Active tracking

`tracked/gog_tracked_refresh.py` queries existing upcoming games with `store_links.store_name = 'gog'`.

For each row it:

1. Reads `store_links.product_id`.
2. Calls `api.gog.com/v2/games/{product_id}`.
3. Parses the result with the same GOG detail parser.
4. Calls `NeonStore.upsert_collected_game()`.

This reuses the existing write path, so changes are recorded through `game_updates`, `source_observations`, `store_links.last_checked_at`, and the `data_jobs` row with `mode = "gog_tracked_refresh"`.

## Operational notes

GOG `releaseStatuses=in:upcoming` reliably identifies products currently marked as coming soon on GOG. Its `releaseDate` can be the original global release date for games that are only upcoming on GOG, and can be historical. The collector therefore only writes a normalized release date when the parsed GOG date is today or in the future; historical dates remain in metadata.
