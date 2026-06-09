from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.http_client import fetch_json_post, fetch_text
from core.models import CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, slugify, strip_tags


XBOX_COMING_SOON_URL = "https://www.xbox.com/en-US/games/browse/DynamicChannel.GamesComingSoon"
XBOX_API_BASE_URL = "https://emerald.xboxservices.com/xboxcomfd"
XBOX_CHANNEL_ID = "DynamicChannel.GamesComingSoon"
XBOX_CHANNEL_KEY = "BROWSE_CHANNELID=DYNAMICCHANNEL.GAMESCOMINGSOON_FILTERS="


@dataclass(frozen=True)
class XboxApiContext:
    locale: str
    ms_cv: str
    channel_key: str


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(
            source_slug="xbox",
            fetched_url=_browse_url("en-US"),
            raw_payload={"page": XBOX_COMING_SOON_URL, "endpoint": _browse_url("en-US"), "pages": []},
            games=[],
        )

    context = fetch_api_context(user_agent)
    games: list[CollectedGame] = []
    pages: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    seen_cursors: set[str] = set()
    encoded_ct: str | None = None

    while len(games) < limit:
        payload = fetch_browse_page(user_agent, context, encoded_ct=encoded_ct)
        pages.append({"encodedCT": encoded_ct, "payload": payload})
        page_games = parse_browse_payload(payload)
        if not page_games:
            break

        for game in page_games:
            key = _game_identity(game)
            if key in seen_games:
                continue
            seen_games.add(key)
            games.append(game)
            if len(games) >= limit:
                break

        next_cursor = _next_encoded_ct(payload, context.channel_key)
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        encoded_ct = next_cursor

    return CollectorResult(
        source_slug="xbox",
        fetched_url=_browse_url(context.locale),
        raw_payload={
            "source": "xbox_emerald_browse",
            "page": XBOX_COMING_SOON_URL,
            "endpoint": _browse_url(context.locale),
            "channelId": XBOX_CHANNEL_ID,
            "channelKey": context.channel_key,
            "locale": context.locale,
            "pages": pages,
        },
        games=games[:limit],
    )


def fetch_api_context(user_agent: str) -> XboxApiContext:
    html = fetch_text(XBOX_COMING_SOON_URL, user_agent)
    state = parse_preloaded_state(html)
    if not state:
        raise RuntimeError("Xbox page did not include window.__PRELOADED_STATE__")
    return _api_context_from_state(state)


def parse_preloaded_state(html: str) -> dict[str, Any]:
    marker = "window.__PRELOADED_STATE__"
    marker_index = html.find(marker)
    if marker_index < 0:
        return {}
    brace_index = html.find("{", marker_index)
    if brace_index < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[brace_index:])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def fetch_browse_page(
    user_agent: str,
    context: XboxApiContext,
    encoded_ct: str | None = None,
) -> Any:
    body: dict[str, Any] = {
        "Filters": "",
        "ReturnFilters": encoded_ct is None,
        "ChannelKeyToBeUsedInResponse": context.channel_key,
        "ChannelId": XBOX_CHANNEL_ID,
    }
    if encoded_ct:
        body["EncodedCT"] = encoded_ct
    return fetch_json_post(
        _browse_url(context.locale),
        json.dumps(body, separators=(",", ":")),
        user_agent,
        headers=_api_headers(context, api_version="1.1"),
    )


def fetch_product_payload(
    product_ids: list[str],
    user_agent: str,
    context: XboxApiContext | None = None,
) -> Any:
    context = context or fetch_api_context(user_agent)
    return fetch_json_post(
        _products_url(context.locale),
        json.dumps({"productIds": product_ids}, separators=(",", ":")),
        user_agent,
        headers=_api_headers(context, api_version="1.0"),
    )


def parse_browse_payload(payload: Any) -> list[CollectedGame]:
    return _games_from_payload(payload, metadata_source="xbox_emerald_browse")


def parse_products_payload(payload: Any) -> list[CollectedGame]:
    return _games_from_payload(payload, metadata_source="xbox_emerald_products")


def _api_context_from_state(state: dict[str, Any]) -> XboxApiContext:
    app_context = _dict_value(state.get("appContext")) or {}
    market_info = _dict_value(app_context.get("marketInfo")) or {}
    telemetry_info = _dict_value(app_context.get("telemetryInfo")) or {}
    ms_cv = _string_value(telemetry_info.get("initialCv"))
    if not ms_cv:
        raise RuntimeError("Xbox page did not include telemetry initialCv")

    channel_data = (
        (_dict_value(state.get("core2")) or {})
        .get("channels")
    )
    channel_data = (_dict_value(channel_data) or {}).get("channelData")
    channel_data = _dict_value(channel_data) or {}
    channel_key = XBOX_CHANNEL_KEY if XBOX_CHANNEL_KEY in channel_data else None
    if not channel_key:
        for key in channel_data:
            if XBOX_CHANNEL_ID.upper() in key.upper():
                channel_key = key
                break
    if not channel_key:
        channel_key = XBOX_CHANNEL_KEY

    return XboxApiContext(
        locale=_string_value(market_info.get("locale")) or "en-US",
        ms_cv=ms_cv,
        channel_key=channel_key,
    )


def _browse_url(locale: str) -> str:
    return f"{XBOX_API_BASE_URL}/browse?locale={locale}"


def _products_url(locale: str) -> str:
    return f"{XBOX_API_BASE_URL}/products?locale={locale}"


def _api_headers(context: XboxApiContext, api_version: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.xbox.com",
        "Referer": XBOX_COMING_SOON_URL,
        "X-MS-API-Version": api_version,
        "MS-CV": context.ms_cv,
    }


def _next_encoded_ct(payload: Any, channel_key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    channels = _dict_value(payload.get("channels")) or {}
    channel = _dict_value(channels.get(channel_key))
    if not channel:
        for key, value in channels.items():
            if XBOX_CHANNEL_ID.upper() in str(key).upper():
                channel = _dict_value(value)
                break
    return _string_value(channel.get("encodedCT") if channel else None)


def _games_from_payload(payload: Any, metadata_source: str) -> list[CollectedGame]:
    if not isinstance(payload, dict):
        return []
    sku_by_product = _items_by_product_id(payload.get("skuSummaries"))
    availability_by_product = _items_by_product_id(payload.get("availabilitySummaries"))
    games: list[CollectedGame] = []
    seen: set[str] = set()
    for product in _dict_list(payload.get("productSummaries")):
        game = _game_from_product_summary(
            product,
            sku_by_product.get(_string_value(product.get("productId")) or "", []),
            availability_by_product.get(_string_value(product.get("productId")) or "", []),
            metadata_source,
        )
        if not game:
            continue
        key = _game_identity(game)
        if key in seen:
            continue
        seen.add(key)
        games.append(game)
    return games


def _game_from_product_summary(
    product: dict[str, Any],
    skus: list[dict[str, Any]],
    availabilities: list[dict[str, Any]],
    metadata_source: str,
) -> CollectedGame | None:
    product_id = _string_value(product.get("productId"))
    title = _clean_text(product.get("title"))
    if not product_id or not title:
        return None

    platforms = _platform_slugs(product.get("availableOn"))
    if not platforms:
        return None

    release_date, accuracy = _parse_xbox_release_date(product.get("releaseDate"))
    source_url = _store_url(title, product_id)
    images = _dict_value(product.get("images")) or {}
    cover_image_url = _image_url(images, "boxArt") or _image_url(images, "poster")
    header_image_url = _image_url(images, "superHeroArt") or cover_image_url
    trailer_url, trailer_thumbnail_url = _video_media(product.get("videos"))
    store_link = _store_link_from_product(product, skus, availabilities, source_url, platforms, metadata_source)
    external_ids = {"xboxProductId": product_id, "productId": product_id}
    if store_link and store_link.sku_id:
        external_ids["xboxSkuId"] = store_link.sku_id

    return CollectedGame(
        title=title,
        source_slug="xbox",
        source_url=source_url,
        platform_slugs=platforms,
        release_date=release_date,
        date_accuracy=accuracy,
        launch_time_utc=None,
        description=_clean_text(product.get("description")),
        short_description=_clean_text(product.get("shortDescription")),
        cover_image_url=cover_image_url,
        header_image_url=header_image_url,
        screenshot_urls=_screenshot_urls(images),
        trailer_url=trailer_url,
        trailer_thumbnail_url=trailer_thumbnail_url,
        publishers=_company_names(product.get("publisherName")),
        developers=_company_names(product.get("developerName")),
        store_links=[store_link] if store_link else [],
        external_ids=external_ids,
    )


def _store_link_from_product(
    product: dict[str, Any],
    skus: list[dict[str, Any]],
    availabilities: list[dict[str, Any]],
    source_url: str,
    platform_slugs: list[str],
    metadata_source: str,
) -> StoreLink | None:
    product_id = _string_value(product.get("productId"))
    if not product_id:
        return None
    sku = _preferred_sku(product, skus, availabilities)
    sku_id = _string_value(sku.get("skuId") if sku else None)
    availability = _preferred_availability(sku_id, availabilities)
    price_text, price, currency = _price_info(availability)
    categories = _string_list(product.get("categories"))

    return StoreLink(
        id=f"xbox_store:{product_id}",
        store_name="xbox_store",
        url=source_url,
        platform_slugs=platform_slugs,
        product_id=product_id,
        sku_id=sku_id,
        edition_name=_clean_text(sku.get("skuTitle") if sku else None),
        price_text=price_text,
        price=price,
        currency=currency,
        preorder_available=True if any(_bool_value(item.get("isPreorder")) is True for item in skus) else None,
        wishlist_available=None,
        demo_available=None,
        release_date_text=_string_value(product.get("releaseDate")),
        metadata=_compact_metadata(
            {
                "source": metadata_source,
                "sourceUrl": source_url,
                "productKind": _string_value(product.get("productKind")),
                "productFamily": _string_value(product.get("productFamily")),
                "availableOn": _string_list(product.get("availableOn")),
                "categories": categories,
                "genres": categories,
                "tags": categories,
                "capabilities": _capabilities(product.get("capabilities")),
                "contentRating": _content_rating(product.get("contentRating")),
                "includedWithPassesProductIds": _string_list(product.get("includedWithPassesProductIds")),
                "languagesSupported": _string_list(product.get("languagesSupported")),
                "preferredSkuId": _string_value(product.get("preferredSkuId")),
                "availabilityId": _string_value(availability.get("availabilityId") if availability else None),
                "availabilityActions": _string_list(availability.get("actions") if availability else None),
                "price": _dict_value(availability.get("price") if availability else None),
                "images": _image_metadata(product.get("images")),
                "videos": _video_metadata(product.get("videos")),
                "skuSummaries": _compact_skus(skus),
                "availabilitySummaries": _compact_availabilities(availabilities),
            }
        ),
    )


def _preferred_sku(
    product: dict[str, Any],
    skus: list[dict[str, Any]],
    availabilities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for sku in skus:
        if _bool_value(sku.get("isPreorder")) is True:
            return sku
    availability_sku_ids = {_string_value(item.get("skuId")) for item in availabilities}
    for sku in skus:
        if _string_value(sku.get("skuId")) in availability_sku_ids:
            return sku
    preferred_sku_id = _string_value(product.get("preferredSkuId"))
    for sku in skus:
        if _string_value(sku.get("skuId")) == preferred_sku_id:
            return sku
    return skus[0] if skus else None


def _preferred_availability(sku_id: str | None, availabilities: list[dict[str, Any]]) -> dict[str, Any] | None:
    if sku_id:
        for item in availabilities:
            if _string_value(item.get("skuId")) == sku_id:
                return item
    return availabilities[0] if availabilities else None


def _price_info(availability: dict[str, Any] | None) -> tuple[str | None, float | None, str | None]:
    price_payload = _dict_value(availability.get("price") if availability else None) or {}
    price = _float_value(price_payload.get("listPrice"), price_payload.get("msrp"))
    currency = _string_value(price_payload.get("currency"))
    if price is None:
        return None, None, currency
    if currency == "USD":
        return f"${price:.2f}", price, currency
    if currency:
        return f"{price:.2f} {currency}", price, currency
    return f"{price:.2f}", price, currency


def _parse_xbox_release_date(value: Any) -> tuple[str | None, str]:
    text = _string_value(value)
    if not text:
        return None, "unknown"
    if "T" in text:
        text = text.split("T", 1)[0]
    return parse_release_date(text)


def _store_url(title: str, product_id: str) -> str:
    return f"https://www.xbox.com/en-US/games/store/{slugify(title)}/{product_id}"


def _platform_slugs(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    slugs: list[str] = []
    for item in values:
        text = _string_value(item)
        normalized = (text or "").lower().replace(" ", "").replace("-", "")
        if normalized in {"xboxseriesx", "xboxseriesxs", "xboxseriesx|s"}:
            slugs.append("xbox-series")
        elif normalized == "xboxone":
            slugs.append("xbox-one")
        elif normalized in {"pc", "windows"}:
            slugs.append("pc")
        elif normalized == "xboxgamepass":
            slugs.append("xbox-game-pass")
    return list(dict.fromkeys(slugs))


def _image_url(images: dict[str, Any], key: str) -> str | None:
    return _first_media_url(images.get(key))


def _screenshot_urls(images: dict[str, Any]) -> list[str]:
    screenshots = images.get("screenshots") or images.get("screenShots")
    values = screenshots if isinstance(screenshots, list) else [screenshots]
    urls = [_first_media_url(item) for item in values]
    return [url for url in list(dict.fromkeys(urls)) if url]


def _video_media(value: Any) -> tuple[str | None, str | None]:
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, dict):
            continue
        url = _string_value(item.get("url") or item.get("uri") or item.get("videoUrl"))
        if url and url.startswith("http"):
            return url, _first_media_url(item.get("thumbnail") or item.get("poster") or item.get("image"))
    return None, None


def _first_media_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, dict):
        for key in ("url", "uri", "imageUrl", "thumbnailUrl"):
            url = _string_value(value.get(key))
            if url and url.startswith("http"):
                return url
        for item in value.values():
            url = _first_media_url(item)
            if url:
                return url
    if isinstance(value, list):
        for item in value:
            url = _first_media_url(item)
            if url:
                return url
    return None


def _image_metadata(value: Any) -> dict[str, Any]:
    images = _dict_value(value) or {}
    metadata: dict[str, Any] = {}
    for key in ("boxArt", "poster", "superHeroArt"):
        url = _first_media_url(images.get(key))
        if url:
            metadata[key] = url
    screenshots = _screenshot_urls(images)
    if screenshots:
        metadata["screenshots"] = screenshots
    return metadata


def _video_metadata(value: Any) -> list[dict[str, str]]:
    videos: list[dict[str, str]] = []
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, dict):
            continue
        url = _string_value(item.get("url") or item.get("uri") or item.get("videoUrl"))
        if not url:
            continue
        payload = {"url": url}
        title = _clean_text(item.get("title"))
        if title:
            payload["title"] = title
        thumbnail = _first_media_url(item.get("thumbnail") or item.get("poster") or item.get("image"))
        if thumbnail:
            payload["thumbnailUrl"] = thumbnail
        videos.append(payload)
    return videos


def _content_rating(value: Any) -> dict[str, Any]:
    rating = _dict_value(value)
    if not rating:
        return {}
    payload = {
        "boardName": _string_value(rating.get("boardName")),
        "rating": _string_value(rating.get("rating")),
        "ratingAge": _int_value(rating.get("ratingAge")),
        "description": _clean_text(rating.get("description") or rating.get("ratingDescription")),
        "descriptors": _string_list(rating.get("descriptors")),
        "interactiveDescriptions": _string_list(rating.get("interactiveDescriptions")),
        "imageUri": _string_value(rating.get("imageUri")),
        "imageLinkUri": _string_value(rating.get("imageLinkUri")),
    }
    return _compact_metadata(payload)


def _capabilities(value: Any) -> dict[str, str]:
    items = _dict_value(value)
    if not items:
        return {}
    capabilities: dict[str, str] = {}
    for key, item in items.items():
        label = _clean_text(item)
        if label:
            capabilities[str(key)] = label
    return capabilities


def _compact_skus(skus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for sku in skus:
        items.append(
            _compact_metadata(
                {
                    "skuId": _string_value(sku.get("skuId")),
                    "skuTitle": _clean_text(sku.get("skuTitle")),
                    "isPreorder": _bool_value(sku.get("isPreorder")),
                    "preferredAvailabilityId": _string_value(sku.get("preferredAvailabilityId")),
                    "optimalAvailabilityId": _string_value(sku.get("optimalAvailabilityId")),
                }
            )
        )
    return [item for item in items if item]


def _compact_availabilities(availabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for availability in availabilities:
        price = _dict_value(availability.get("price")) or {}
        items.append(
            _compact_metadata(
                {
                    "availabilityId": _string_value(availability.get("availabilityId")),
                    "skuId": _string_value(availability.get("skuId")),
                    "actions": _string_list(availability.get("actions")),
                    "endDateUtc": _string_value(availability.get("endDateUtc")),
                    "price": _compact_metadata(
                        {
                            "listPrice": _float_value(price.get("listPrice")),
                            "msrp": _float_value(price.get("msrp")),
                            "currency": _string_value(price.get("currency")),
                            "discountPercentage": _float_value(price.get("discountPercentage")),
                        }
                    ),
                }
            )
        )
    return [item for item in items if item]


def _items_by_product_id(value: Any) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {}
    for item in _dict_list(value):
        product_id = _string_value(item.get("productId"))
        if not product_id:
            continue
        items.setdefault(product_id, []).append(item)
    return items


def _game_identity(game: CollectedGame) -> str:
    for link in game.store_links:
        if link.id:
            return link.id
    return game.source_url


def _compact_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_value(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            items.append(text)
    return list(dict.fromkeys(items))


def _company_names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in values:
        name = _clean_text(item)
        if name:
            names.append(" ".join(name.split()))
    return list(dict.fromkeys(names))


def _clean_text(value: Any) -> str | None:
    text = _string_value(value)
    if not text:
        return None
    return strip_tags(text) or None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _int_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _float_value(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None
