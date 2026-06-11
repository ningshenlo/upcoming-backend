# Nintendo upcoming channel

## Discovery source

Nintendo US store uses an Algolia index behind the official coming-soon page.

- Page: `https://www.nintendo.com/us/store/games/coming-soon/`
- API endpoint: `https://U3B6GR4UA3-dsn.algolia.net/1/indexes/store_game_en_us/query`
- App ID: `U3B6GR4UA3`
- Search API key: `a29c6927638bfd8cee23993e51e721c9`
- Index: `store_game_en_us`

Request body used by `collectors/nintendo.py`:

```json
{
  "query": "",
  "hitsPerPage": 40,
  "page": 0,
  "distinct": true,
  "facets": ["*"],
  "attributesToHighlight": ["description"],
  "filters": "availability:\"Coming soon\""
}
```

`distinct=true` returns the main upcoming products. `distinct=false` also returns edition and upgrade-pack variants.

## Parsed fields

The collector maps each Algolia hit into the shared `CollectedGame` structure.

- `title` -> `CollectedGame.title`
- `url` / `urlKey` -> `CollectedGame.source_url`
- `releaseDateDisplay` / `releaseDate` -> `release_date` and `date_accuracy`
- `platform` / `platformCode` -> `nintendo-switch` or `nintendo-switch-2`
- `description`, `productImage`, `productGallery` -> description and media fields
- `softwarePublisher`, `softwareDeveloper` -> company fields
- `nsuid`, `sku`, `urlKey` -> `external_ids`

The collector also writes a `StoreLink` with:

- `store_name = "nintendo_eshop"`
- `product_id = nsuid`
- `sku_id = sku`
- price, preorder availability, demo availability, release date text
- metadata for ESRB rating, descriptors, genres, features, NSO features, gallery, availability, eShop details, and platform code

## Active tracking

`tracked/nintendo_tracked_refresh.py` queries existing upcoming games with `store_links.store_name = 'nintendo_eshop'`.

For each tracked product it:

1. Fetches the saved Nintendo product page URL.
2. Parses the page `__NEXT_DATA__` / Apollo product payload.
3. Selects the product matching the stored `nsuid`, `sku`, or URL.
4. Calls `NeonStore.upsert_collected_game()`.

This uses the same storage path as discovery, so changes are recorded through `game_updates`, `source_observations`, `store_links.last_checked_at`, and the `data_jobs` row with `mode = "nintendo_tracked_refresh"`.

## Notes

The Algolia endpoint is an official Nintendo frontend dependency, not a documented public partner API. If Nintendo changes the index name, app key, or response shape, the existing source failure flow should flag the collector. The coming-soon/product-page `__NEXT_DATA__` parser remains useful for investigation and active tracking.
