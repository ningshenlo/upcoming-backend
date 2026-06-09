from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

from core.http_client import fetch_json
from core.models import CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, strip_tags


GOG_CATALOG_URL = "https://catalog.gog.com/v1/catalog"
GOG_UPCOMING_URL = "https://www.gog.com/en/games?releaseStatuses=upcoming&order=asc:releaseDate"
GOG_GAME_DETAIL_URL = "https://api.gog.com/v2/games/{product_id}?locale=en-US&countryCode=US&currencyCode=USD"
GOG_PAGE_SIZE = 48
GOG_PLATFORM_SLUGS = ["pc", "gog"]


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(
            source_slug="gog",
            fetched_url=catalog_url(page=1, count=GOG_PAGE_SIZE),
            raw_payload={"source": "gog_catalog", "page": GOG_UPCOMING_URL, "pages": []},
            games=[],
        )

    games: list[CollectedGame] = []
    pages: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    seen: set[str] = set()
    page = 1

    while len(games) < limit:
        url = catalog_url(page=page, count=GOG_PAGE_SIZE)
        payload = fetch_catalog_page(page=page, count=GOG_PAGE_SIZE, user_agent=user_agent)
        pages.append({"url": url, "payload": payload})
        page_games = parse_catalog_payload(payload)
        if not page_games:
            break

        for game in page_games:
            key = _game_identity(game)
            if key in seen:
                continue
            seen.add(key)

            product_id = _string_value(game.external_ids.get("gogProductId"))
            if product_id:
                detail_url = GOG_GAME_DETAIL_URL.format(product_id=product_id)
                try:
                    detail_payload = fetch_game_payload(product_id, user_agent)
                    details[product_id] = {"url": detail_url, "payload": detail_payload}
                    enriched = apply_game_payload(game, detail_payload)
                    if enriched is None:
                        continue
                    game = enriched
                except Exception as exc:
                    details[product_id] = {"url": detail_url, "error": str(exc)}

            games.append(game)
            if len(games) >= limit:
                break

        total_pages = _int_value(payload.get("pages")) if isinstance(payload, dict) else None
        if total_pages is not None and page >= total_pages:
            break
        page += 1

    raw_payload: dict[str, Any] = {
        "source": "gog_catalog",
        "page": GOG_UPCOMING_URL,
        "endpoint": GOG_CATALOG_URL,
        "locale": "en-US",
        "country": "US",
        "currency": "USD",
        "pages": pages,
    }
    if details:
        raw_payload["details"] = details

    return CollectorResult(
        source_slug="gog",
        fetched_url=pages[0]["url"] if pages else catalog_url(page=1, count=GOG_PAGE_SIZE),
        raw_payload=raw_payload,
        games=games[:limit],
    )


def catalog_url(page: int, count: int = GOG_PAGE_SIZE) -> str:
    return GOG_CATALOG_URL + "?" + urlencode(
        {
            "limit": count,
            "page": page,
            "order": "asc:releaseDate",
            "productType": "in:game",
            "releaseStatuses": "in:upcoming",
            "countryCode": "US",
            "locale": "en-US",
            "currencyCode": "USD",
        }
    )


def fetch_catalog_page(page: int, count: int, user_agent: str) -> Any:
    return fetch_json(catalog_url(page=page, count=count), user_agent)


def fetch_game_payload(product_id: str, user_agent: str) -> Any:
    return fetch_json(GOG_GAME_DETAIL_URL.format(product_id=product_id), user_agent)


def parse_catalog_payload(payload: Any) -> list[CollectedGame]:
    products = payload.get("products") if isinstance(payload, dict) else None
    return _games_from_products(products, metadata_source="gog_catalog")


def parse_game_payload(payload: Any) -> list[CollectedGame]:
    game = _game_from_detail(payload, metadata_source="gog_game_detail")
    return [game] if game else []


def apply_game_payload(game: CollectedGame, payload: Any) -> CollectedGame | None:
    detail_games = parse_game_payload(payload)
    if not detail_games:
        return None
    detail = detail_games[0]
    store_link = _merge_store_link(
        game.store_links[0] if game.store_links else None,
        detail.store_links[0] if detail.store_links else None,
    )
    external_ids = dict(game.external_ids)
    external_ids.update(detail.external_ids)
    return replace(
        game,
        release_date=detail.release_date or game.release_date,
        date_accuracy=detail.date_accuracy if detail.release_date else game.date_accuracy,
        launch_time_utc=detail.launch_time_utc or game.launch_time_utc,
        description=detail.description or game.description,
        short_description=detail.short_description or game.short_description,
        cover_image_url=detail.cover_image_url or game.cover_image_url,
        header_image_url=detail.header_image_url or game.header_image_url,
        screenshot_urls=detail.screenshot_urls or game.screenshot_urls,
        trailer_url=detail.trailer_url or game.trailer_url,
        trailer_thumbnail_url=detail.trailer_thumbnail_url or game.trailer_thumbnail_url,
        publishers=detail.publishers or game.publishers,
        developers=detail.developers or game.developers,
        store_links=[store_link] if store_link else game.store_links,
        external_ids=external_ids,
    )


def _games_from_products(value: Any, metadata_source: str) -> list[CollectedGame]:
    if not isinstance(value, list):
        return []
    games: list[CollectedGame] = []
    seen: set[str] = set()
    for product in value:
        if not isinstance(product, dict):
            continue
        game = _game_from_catalog_product(product, metadata_source)
        if not game:
            continue
        key = _game_identity(game)
        if key in seen:
            continue
        seen.add(key)
        games.append(game)
    return games


def _game_from_catalog_product(product: dict[str, Any], metadata_source: str) -> CollectedGame | None:
    product_id = _string_value(product.get("id"))
    title = _clean_text(product.get("title"))
    product_type = (_string_value(product.get("productType")) or "").lower()
    if not product_id or not title or product_type != "game":
        return None

    source_url = _source_url(product)
    if not source_url:
        return None

    release_date_text = _string_value(product.get("releaseDate"))
    release_date, date_accuracy = _gog_release_date(release_date_text)
    price_text, price, currency = _price_info(product.get("price"))
    tags = _named_items(product.get("tags"))
    genres = _named_items(product.get("genres"))
    features = _named_items(product.get("features"))
    operating_systems = _string_list(product.get("operatingSystems"))
    slug = _string_value(product.get("slug"))
    store_link = StoreLink(
        id=f"gog:{product_id}",
        store_name="gog",
        url=source_url,
        platform_slugs=GOG_PLATFORM_SLUGS,
        product_id=product_id,
        sku_id=slug,
        edition_name="GOG store page",
        price_text=price_text,
        price=price,
        currency=currency,
        preorder_available=True if price is not None else None,
        release_date_text=release_date_text,
        metadata=_compact_metadata(
            {
                "source": metadata_source,
                "sourceUrl": source_url,
                "productId": product_id,
                "slug": slug,
                "productState": _string_value(product.get("productState")),
                "productType": product_type,
                "releaseDate": release_date_text,
                "storeReleaseDate": _string_value(product.get("storeReleaseDate")),
                "genres": genres,
                "tags": tags,
                "features": features,
                "operatingSystems": operating_systems,
                "ratings": product.get("ratings") if isinstance(product.get("ratings"), list) else None,
                "editions": product.get("editions") if isinstance(product.get("editions"), list) else None,
            }
        ),
    )

    external_ids = {"gogProductId": product_id, "productId": product_id}
    if slug:
        external_ids["gogSlug"] = slug

    return CollectedGame(
        title=title,
        source_slug="gog",
        source_url=source_url,
        platform_slugs=GOG_PLATFORM_SLUGS,
        release_date=release_date,
        date_accuracy=date_accuracy,
        description=None,
        short_description=None,
        cover_image_url=_image_url(product.get("coverVertical")) or _image_url(product.get("coverHorizontal")),
        header_image_url=_image_url(product.get("coverHorizontal")) or _image_url(product.get("galaxyBackgroundImage")),
        screenshot_urls=_image_list(product.get("screenshots")),
        publishers=_string_list(product.get("publishers")),
        developers=_string_list(product.get("developers")),
        store_links=[store_link],
        external_ids=external_ids,
    )


def _game_from_detail(payload: Any, metadata_source: str) -> CollectedGame | None:
    if not isinstance(payload, dict):
        return None
    release_status = (_string_value(payload.get("releaseStatus")) or "").lower()
    if release_status and release_status != "coming-soon":
        return None

    embedded = _dict_value(payload.get("_embedded")) or {}
    product = _dict_value(embedded.get("product")) or {}
    product_id = _string_value(product.get("id"))
    title = _clean_text(product.get("title"))
    category = (_string_value(product.get("category")) or "").lower()
    if not product_id or not title or (category and category != "game"):
        return None

    source_url = _link_href(payload.get("_links"), "store") or _store_url(product_id)
    release_date_text = _string_value(product.get("globalReleaseDate"))
    release_date, date_accuracy = _gog_release_date(release_date_text)
    launch_time_utc = _launch_time_utc(release_date_text) if release_date else None
    publisher = _dict_value(embedded.get("publisher")) or {}
    developers = _named_items(embedded.get("developers"))
    tags = _named_items(embedded.get("tags"))
    features = _named_items(embedded.get("features"))
    operating_systems = _operating_systems(embedded.get("supportedOperatingSystems"))
    trailer_url, trailer_thumbnail_url = _video_media(embedded.get("videos"))
    store_link = StoreLink(
        id=f"gog:{product_id}",
        store_name="gog",
        url=source_url,
        platform_slugs=GOG_PLATFORM_SLUGS,
        product_id=product_id,
        edition_name="GOG store page",
        preorder_available=product.get("isPreorder") if isinstance(product.get("isPreorder"), bool) else None,
        wishlist_available=None,
        demo_available=None,
        release_date_text=release_date_text,
        metadata=_compact_metadata(
            {
                "source": metadata_source,
                "sourceUrl": source_url,
                "productId": product_id,
                "releaseStatus": release_status,
                "globalReleaseDate": release_date_text,
                "gogReleaseDate": _string_value(product.get("gogReleaseDate")),
                "category": category,
                "isAvailableForSale": product.get("isAvailableForSale")
                if isinstance(product.get("isAvailableForSale"), bool)
                else None,
                "isPreorder": product.get("isPreorder") if isinstance(product.get("isPreorder"), bool) else None,
                "inDevelopment": _dict_value(payload.get("inDevelopment")),
                "tags": tags,
                "features": features,
                "operatingSystems": operating_systems,
            }
        ),
    )

    return CollectedGame(
        title=title,
        source_slug="gog",
        source_url=source_url,
        platform_slugs=GOG_PLATFORM_SLUGS,
        release_date=release_date,
        date_accuracy=date_accuracy,
        launch_time_utc=launch_time_utc,
        description=_clean_text(payload.get("overview") or payload.get("description")),
        short_description=None,
        cover_image_url=_link_href(payload.get("_links"), "boxArtImage") or _detail_image(product),
        header_image_url=_link_href(payload.get("_links"), "galaxyBackgroundImage")
        or _link_href(payload.get("_links"), "backgroundImage"),
        screenshot_urls=_detail_screenshots(embedded.get("screenshots")),
        trailer_url=trailer_url,
        trailer_thumbnail_url=trailer_thumbnail_url,
        publishers=_string_list([publisher.get("name")]),
        developers=developers,
        store_links=[store_link],
        external_ids={"gogProductId": product_id, "productId": product_id},
    )


def _merge_store_link(base: StoreLink | None, detail: StoreLink | None) -> StoreLink | None:
    if base is None:
        return detail
    if detail is None:
        return base
    metadata = dict(base.metadata or {})
    metadata.update(detail.metadata or {})
    return replace(
        base,
        preorder_available=detail.preorder_available if detail.preorder_available is not None else base.preorder_available,
        wishlist_available=detail.wishlist_available if detail.wishlist_available is not None else base.wishlist_available,
        demo_available=detail.demo_available if detail.demo_available is not None else base.demo_available,
        release_date_text=detail.release_date_text or base.release_date_text,
        metadata=metadata,
    )


def _source_url(product: dict[str, Any]) -> str | None:
    url = _string_value(product.get("storeLink"))
    if url and url.startswith("http"):
        return url
    slug = _string_value(product.get("slug"))
    if slug:
        return f"https://www.gog.com/en/game/{slug}"
    product_id = _string_value(product.get("id"))
    return _store_url(product_id) if product_id else None


def _store_url(product_id: str) -> str:
    return f"https://www.gog.com/en/game/{product_id}"


def _gog_release_date(value: Any) -> tuple[str | None, str]:
    text = _string_value(value)
    if not text:
        return None, "unknown"
    normalized = text
    if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", text):
        normalized = text.replace(".", "-")
    elif "T" in text:
        normalized = text.split("T", 1)[0]
    parsed_date, accuracy = parse_release_date(normalized)
    if accuracy == "exact" and parsed_date and date.fromisoformat(parsed_date) >= date.today():
        return parsed_date, accuracy
    return None, "unknown"


def _launch_time_utc(value: Any) -> str | None:
    text = _string_value(value)
    if not text or "T" not in text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _price_info(value: Any) -> tuple[str | None, float | None, str | None]:
    price = _dict_value(value) or {}
    final_money = _dict_value(price.get("finalMoney")) or {}
    amount = _float_value(final_money.get("amount"))
    currency = _string_value(final_money.get("currency"))
    return _string_value(price.get("final")), amount, currency


def _video_media(value: Any) -> tuple[str | None, str | None]:
    videos = value if isinstance(value, list) else []
    for video in videos:
        if not isinstance(video, dict):
            continue
        links = _dict_value(video.get("_links")) or {}
        url = _link_href(links, "self")
        thumbnail = _link_href(links, "thumbnail")
        if url:
            return url, thumbnail
    return None, None


def _detail_screenshots(value: Any) -> list[str]:
    screenshots = value if isinstance(value, list) else []
    urls = [_link_href((_dict_value(item) or {}).get("_links"), "self") for item in screenshots]
    return [_image_url(url) for url in list(dict.fromkeys(urls)) if url]


def _detail_image(product: dict[str, Any]) -> str | None:
    links = _dict_value(product.get("_links")) or {}
    return _image_url(_link_href(links, "image"))


def _image_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    urls = [_image_url(item) for item in values]
    return [url for url in list(dict.fromkeys(urls)) if url]


def _image_url(value: Any) -> str | None:
    url = _string_value(value)
    if not url or not url.startswith("http"):
        return None
    return url.replace("{formatter}", "1600")


def _link_href(links: Any, key: str) -> str | None:
    link = (_dict_value(links) or {}).get(key)
    href = _string_value((_dict_value(link) or {}).get("href"))
    return _image_url(href) or href


def _operating_systems(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    systems: list[str] = []
    for item in values:
        system = _dict_value((_dict_value(item) or {}).get("operatingSystem")) or {}
        name = _string_value(system.get("name"))
        if name:
            systems.append(name)
    return list(dict.fromkeys(systems))


def _named_items(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    names: list[str] = []
    for item in values:
        if isinstance(item, dict):
            name = _clean_text(item.get("name"))
        else:
            name = _clean_text(item)
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    result = [_clean_text(item) for item in values]
    return [item for item in list(dict.fromkeys(result)) if item]


def _clean_text(value: Any) -> str | None:
    text = _string_value(value)
    if not text:
        return None
    return strip_tags(text) or None


def _game_identity(game: CollectedGame) -> str:
    for link in game.store_links:
        if link.id:
            return link.id
    return game.source_url


def _dict_value(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


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


def _float_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _compact_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
