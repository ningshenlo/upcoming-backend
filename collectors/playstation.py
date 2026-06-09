from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from html import unescape
from typing import Any

from core.http_client import fetch_json_post, fetch_text
from core.models import CollectedEvent, CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, strip_tags


PLAYSTATION_GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
PLAYSTATION_CATEGORY_URL = "https://store.playstation.com/en-us/category/82ced94c-ed3f-4d81-9b50-4d4cf1da170b"
PS5_COMING_SOON_CATEGORY_ID = "82ced94c-ed3f-4d81-9b50-4d4cf1da170b"
PS5_CATEGORY_GRID_HASH = "9845afc0dbaab4965f6563fffc703f588c8e76792000e8610843b8d3ee9c4c09"
CONCEPT_DETAIL_HASH = "cc90404ac049d935afbd9968aef523da2b6723abfb9d586e5f77ebf7c5289006"
PAGE_SIZE = 24
ENV_SCRIPT_RE = re.compile(r'<script id="env:[^"]+" type="application/json">(?P<body>.*?)</script>', re.DOTALL)


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(
            source_slug="playstation",
            fetched_url=PLAYSTATION_CATEGORY_URL,
            raw_payload={"pages": [], "details": {}},
            games=[],
        )

    games: list[CollectedGame] = []
    seen_concept_ids: set[str] = set()
    pages: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    store_pages: dict[str, Any] = {}
    offset = 0

    while len(games) < limit:
        page_payload = _fetch_category_page(offset, user_agent)
        pages.append({"offset": offset, "payload": page_payload})
        concepts, page_info = parse_category_payload(page_payload)
        if not concepts:
            break

        for concept in concepts:
            concept_id = _string_value(concept.get("id"))
            if not concept_id or concept_id in seen_concept_ids:
                continue
            seen_concept_ids.add(concept_id)

            detail_payload = _fetch_concept_detail(concept_id, user_agent)
            details[concept_id] = detail_payload
            game = parse_concept_detail_payload(detail_payload, fallback=concept)
            if game is None:
                continue
            try:
                store_page_html = fetch_text(game.source_url, user_agent)
                store_pages[concept_id] = {"url": game.source_url, "html": store_page_html}
                game = apply_concept_page_store_links(game, store_page_html)
            except Exception as exc:
                store_pages[concept_id] = {"url": game.source_url, "error": str(exc)}
            games.append(game)
            if len(games) >= limit:
                break

        if _is_last_page(page_info, offset, PAGE_SIZE, len(concepts)):
            break
        offset += PAGE_SIZE

    return CollectorResult(
        source_slug="playstation",
        fetched_url=PLAYSTATION_CATEGORY_URL,
        raw_payload={"pages": pages, "details": details, "store_pages": store_pages},
        games=games[:limit],
    )


def parse_category_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grid = _find_key(payload, "categoryGridRetrieve")
    if not isinstance(grid, dict):
        return [], {}

    concepts = grid.get("concepts")
    page_info = grid.get("pageInfo")
    if not isinstance(concepts, list):
        concepts = []
    if not isinstance(page_info, dict):
        page_info = {}
    return [item for item in concepts if isinstance(item, dict)], page_info


def parse_concept_detail_payload(payload: Any, fallback: dict[str, Any] | None = None) -> CollectedGame | None:
    concept = _find_key(payload, "conceptRetrieve")
    if not isinstance(concept, dict):
        concept = fallback or {}
    return _game_from_concept(concept, fallback or {})


def apply_concept_page_store_links(game: CollectedGame, html: str) -> CollectedGame:
    store_links = parse_concept_page_store_links(html, game.source_url)
    if not store_links:
        return game
    return replace(game, store_links=store_links, events=_events_from_store_links(game, store_links))


def parse_concept_page_store_links(html: str, source_url: str) -> list[StoreLink]:
    cache, translations = _page_env_cache(html)
    if not cache:
        return []

    concept_id = _concept_id_from_url(source_url)
    concept = _page_concept(cache, concept_id)
    default_product_ref = _ref_key(concept.get("defaultProduct")) if concept else None
    wishlist_available = _bool_value(concept.get("isWishlistable")) if concept else None
    release_date_text = _release_date_text(concept.get("releaseDate")) if concept else None

    product_refs: list[str] = []
    if default_product_ref:
        product_refs.append(default_product_ref)
    if concept:
        for item in _ref_values(concept.get("products")):
            ref = _ref_key(item)
            if ref:
                product_refs.append(ref)
    if not product_refs:
        product_refs = [key for key, value in cache.items() if key.startswith("Product:") and isinstance(value, dict)]

    products: list[tuple[int, int, dict[str, Any]]] = []
    seen_product_ids: set[str] = set()
    for ref in product_refs:
        product = _resolve_ref(ref, cache)
        if not isinstance(product, dict):
            continue
        product_id = _string_value(product.get("id"))
        if not product_id or product_id in seen_product_ids:
            continue
        seen_product_ids.add(product_id)
        platforms = _platform_slugs(product.get("platforms"))
        if "ps5" not in platforms:
            continue
        edition = _dict_value(product.get("edition")) or {}
        ordering = _int_value(edition.get("ordering")) or 99
        is_default = 0 if f"Product:{product_id}" == default_product_ref else 1
        products.append((is_default, ordering, product))

    links: list[StoreLink] = []
    for _, _, product in sorted(products, key=lambda item: (item[0], item[1])):
        link = _store_link_from_product(product, cache, translations, source_url, wishlist_available, release_date_text)
        if link:
            links.append(link)
    return links


def _events_from_store_links(game: CollectedGame, store_links: list[StoreLink]) -> list[CollectedEvent]:
    events = list(game.events)
    if not any(link.demo_available is True for link in store_links):
        return events

    platform_slugs: list[str] = []
    for link in store_links:
        if link.demo_available is True:
            platform_slugs.extend(link.platform_slugs)
    if not platform_slugs:
        platform_slugs = game.platform_slugs

    demo_event = CollectedEvent(
        event_type="demo",
        title=f"{game.title} demo",
        platform_slugs=list(dict.fromkeys(platform_slugs)),
        status="confirmed",
        confidence=85,
        source_url=game.source_url,
    )
    keys = {
        (
            event.event_type,
            tuple(event.platform_slugs),
            event.date,
            event.source_url,
        )
        for event in events
    }
    key = (demo_event.event_type, tuple(demo_event.platform_slugs), demo_event.date, demo_event.source_url)
    if key not in keys:
        events.append(demo_event)
    return events


def _fetch_category_page(offset: int, user_agent: str) -> Any:
    return _fetch_graphql(
        "categoryGridRetrieve",
        PS5_CATEGORY_GRID_HASH,
        {
            "id": PS5_COMING_SOON_CATEGORY_ID,
            "pageArgs": {"size": PAGE_SIZE, "offset": offset},
            "sortBy": None,
            "filterBy": [],
            "facetOptions": [],
        },
        user_agent,
    )


def _fetch_concept_detail(concept_id: str, user_agent: str) -> Any:
    return _fetch_graphql(
        "metGetConceptById",
        CONCEPT_DETAIL_HASH,
        {"conceptId": concept_id, "productId": ""},
        user_agent,
    )


def _fetch_graphql(operation_name: str, sha256_hash: str, variables: dict[str, Any], user_agent: str) -> Any:
    body = json.dumps(
        {
            "operationName": operation_name,
            "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}},
        },
        separators=(",", ":"),
    )
    return fetch_json_post(
        PLAYSTATION_GRAPHQL_URL,
        body,
        user_agent,
        headers={
            "Content-Type": "application/json",
            "x-apollo-operation-name": operation_name,
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://store.playstation.com",
            "Referer": "https://store.playstation.com/en-us/",
        },
    )


def _game_from_concept(concept: dict[str, Any], fallback: dict[str, Any]) -> CollectedGame | None:
    default_product = _dict_value(concept.get("defaultProduct")) or _first_product(concept) or _first_product(fallback)
    platforms = _platform_slugs(default_product.get("platforms") if default_product else None)
    if "ps5" not in platforms:
        return None

    product_type = _string_value(default_product.get("type") if default_product else None)
    subtype = _string_value(default_product.get("subType") if default_product else None)
    if product_type and product_type.upper() != "GAME":
        return None
    if subtype and subtype.upper() not in {"FULL_GAME", "GAME"}:
        return None

    title = _string_value(concept.get("name")) or _string_value(fallback.get("name"))
    concept_id = _string_value(concept.get("id")) or _string_value(fallback.get("id"))
    if not title or not concept_id:
        return None

    release_value = concept.get("releaseDate") or (default_product.get("releaseDate") if default_product else None)
    release_date, accuracy = _parse_ps_date(release_value)
    product_id = _string_value(default_product.get("id") if default_product else None)
    np_title_id = _string_value(default_product.get("npTitleId") if default_product else None)
    concept_media = concept.get("media")
    product_media = default_product.get("media") if default_product else None
    image_url = _media_url_by_role(concept_media, ["MASTER", "GAMEHUB_COVER_ART", "PORTRAIT_BANNER"])
    header_image_url = _media_url_by_role(
        concept_media,
        ["BACKGROUND_LAYER_ART", "SIXTEEN_BY_NINE_BANNER", "FOUR_BY_THREE_BANNER"],
    )
    trailer_url, trailer_thumbnail_url = _video_media(product_media) or _video_media(concept_media) or (None, None)

    external_ids = {"playstationConceptId": concept_id}
    if product_id:
        external_ids["playstationProductId"] = product_id
    if np_title_id:
        external_ids["npTitleId"] = np_title_id

    return CollectedGame(
        title=title,
        source_slug="playstation",
        source_url=f"https://store.playstation.com/en-us/concept/{concept_id}",
        platform_slugs=platforms,
        release_date=release_date,
        date_accuracy=accuracy,
        launch_time_utc=_parse_ps_datetime(release_value),
        description=_description(concept),
        short_description=_description(concept, preferred_types=("SHORT",)),
        cover_image_url=image_url,
        header_image_url=header_image_url,
        screenshot_urls=_screenshot_urls(concept_media),
        trailer_url=trailer_url,
        trailer_thumbnail_url=trailer_thumbnail_url,
        publishers=_company_names(concept.get("publisherName") or (default_product.get("publisherName") if default_product else None)),
        external_ids=external_ids,
    )


def _platform_slugs(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    slugs: list[str] = []
    for item in values:
        text = _string_value(item)
        if not text and isinstance(item, dict):
            text = _string_value(item.get("name") or item.get("platform") or item.get("label"))
        normalized = (text or "").lower().replace(" ", "").replace("-", "")
        if normalized in {"ps5", "playstation5"}:
            slugs.append("ps5")
        elif normalized in {"ps4", "playstation4"}:
            slugs.append("ps4")
    return list(dict.fromkeys(slugs))


def _parse_ps_date(value: Any) -> tuple[str | None, str]:
    if isinstance(value, dict):
        parsed_date, accuracy = _parse_ps_date(value.get("value") or value.get("date"))
        release_type = _string_value(value.get("type"))
        if parsed_date and release_type:
            accuracy = _release_date_accuracy(release_type, accuracy)
        return parsed_date, accuracy
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(), "exact"
    if isinstance(value, str):
        cleaned = value.strip()
        if "T" in cleaned:
            try:
                return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date().isoformat(), "exact"
            except ValueError:
                return parse_release_date(cleaned.split("T", 1)[0])
        return parse_release_date(cleaned)
    return None, "unknown"


def _release_date_accuracy(release_type: str, fallback: str) -> str:
    normalized = release_type.upper()
    if normalized == "DAY_MONTH_YEAR":
        return "exact"
    if normalized == "MONTH_YEAR":
        return "month"
    if normalized == "YEAR":
        return "year"
    if normalized == "QUARTER":
        return "quarter"
    return fallback


def _is_last_page(page_info: dict[str, Any], offset: int, page_size: int, parsed_count: int) -> bool:
    if page_info.get("isLast") is True:
        return True
    total_count = _int_value(page_info.get("totalCount"))
    if total_count is not None and offset + page_size >= total_count:
        return True
    return parsed_count < page_size


def _first_product(value: dict[str, Any]) -> dict[str, Any] | None:
    products = value.get("products")
    if isinstance(products, list):
        for item in products:
            if isinstance(item, dict):
                return item
    return None


def _page_env_cache(html: str) -> tuple[dict[str, Any], dict[str, str]]:
    cache: dict[str, Any] = {}
    translations: dict[str, str] = {}
    for match in ENV_SCRIPT_RE.finditer(html or ""):
        try:
            data = json.loads(unescape(match.group("body")))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        env_cache = data.get("cache")
        if isinstance(env_cache, dict):
            for key, value in env_cache.items():
                cache[key] = _merge_cache_value(cache.get(key), value)
        env_translations = data.get("translations")
        if isinstance(env_translations, dict):
            translations.update({str(key): str(value) for key, value in env_translations.items()})
    return cache, translations


def _merge_cache_value(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming if incoming not in (None, [], {}) else existing
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, [], {}):
            continue
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_cache_value(merged[key], value)
        else:
            merged[key] = value
    return merged


def _page_concept(cache: dict[str, Any], concept_id: str | None) -> dict[str, Any] | None:
    if concept_id:
        concept = cache.get(f"Concept:{concept_id}")
        if isinstance(concept, dict):
            return concept
    for key, value in cache.items():
        if key.startswith("Concept:") and isinstance(value, dict):
            return value
    return None


def _store_link_from_product(
    product: dict[str, Any],
    cache: dict[str, Any],
    translations: dict[str, str],
    source_url: str,
    wishlist_available: bool | None,
    release_date_text: str | None,
) -> StoreLink | None:
    product_id = _string_value(product.get("id"))
    if not product_id:
        return None

    cta = _product_cta(product, cache)
    sku_id = _sku_id(product, cta)
    local = _dict_value(cta.get("local") if cta else None) or {}
    price_text, price, currency = _price_info(local)
    cta_label_key = _string_value(local.get("ctaLabel"))
    cta_label = translations.get(cta_label_key or "", cta_label_key)
    edition = _dict_value(product.get("edition")) or {}
    edition_features = _string_list(edition.get("features"))
    cta_type = _string_value(cta.get("type") if cta else None)
    cta_id = _string_value(cta.get("id") if cta else None)

    return StoreLink(
        id=f"playstation_store:{product_id}",
        store_name="playstation_store",
        url=f"https://store.playstation.com/en-us/product/{product_id}",
        platform_slugs=_platform_slugs(product.get("platforms")),
        product_id=product_id,
        sku_id=sku_id,
        np_title_id=_string_value(product.get("npTitleId")),
        edition_name=_string_value(edition.get("name")),
        edition_type=_string_value(edition.get("type")),
        edition_features=edition_features,
        price_text=price_text,
        price=price,
        currency=currency,
        preorder_available=_has_token("preorder", cta_type, cta_id, cta_label, json.dumps(local)),
        wishlist_available=wishlist_available,
        demo_available=_has_token("demo", cta_type, cta_id, cta_label),
        release_date_text=_release_date_text(product.get("releaseDate")) or release_date_text,
        metadata={
            "source": "playstation_env_cache",
            "sourceUrl": source_url,
            "ctaLabel": cta_label,
            "ctaType": _string_value(local.get("ctaType")),
            "storeDisplayClassification": _string_value(product.get("storeDisplayClassification")),
        },
    )


def _product_cta(product: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any] | None:
    ctas = [item for item in _resolved_items(product.get("webctas"), cache) if isinstance(item, dict)]
    sku_id = _sku_id(product, ctas[0] if ctas else None)
    if sku_id:
        ctas.extend(
            value
            for key, value in cache.items()
            if key.startswith("GameCTA:") and sku_id in key and isinstance(value, dict)
        )
    if not ctas:
        return None
    return sorted(ctas, key=lambda item: 0 if isinstance(item.get("local"), dict) else 1)[0]


def _sku_id(product: dict[str, Any], cta: dict[str, Any] | None) -> str | None:
    if cta:
        action = _dict_value(cta.get("action")) or {}
        params = action.get("param")
        if isinstance(params, list):
            for item in params:
                if isinstance(item, dict) and item.get("name") == "skuId":
                    sku_id = _string_value(item.get("value"))
                    if sku_id:
                        return sku_id
        local = _dict_value(cta.get("local")) or {}
        cta_data_track = _dict_value(local.get("ctaDataTrack")) or {}
        sku_id = _string_value(cta_data_track.get("sku"))
        if sku_id:
            return sku_id
        telemetry = _dict_value(local.get("telemetryMeta")) or {}
        sku_detail = _dict_value(telemetry.get("skuDetail")) or {}
        sku_id = _string_value(sku_detail.get("skuId"))
        if sku_id:
            return sku_id

    for item in _ref_values(product.get("skus")):
        resolved = _resolve_ref(item, {})
        if isinstance(resolved, dict):
            sku_id = _string_value(resolved.get("id"))
            if sku_id:
                return sku_id
        ref = _ref_key(item)
        if ref and ref.startswith("Sku:"):
            return ref.split(":", 1)[1]
    return None


def _price_info(local: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    price_text = _string_value(local.get("priceOrText"))
    telemetry = _dict_value(local.get("telemetryMeta")) or {}
    sku_detail = _dict_value(telemetry.get("skuDetail")) or {}
    price_details = sku_detail.get("skuPriceDetail")
    if isinstance(price_details, list):
        for item in price_details:
            if not isinstance(item, dict):
                continue
            cents = _int_value(item.get("discountPriceValue")) or _int_value(item.get("originalPriceValue"))
            currency = _string_value(item.get("priceCurrencyCode"))
            formatted = _string_value(item.get("discountPriceFormatted") or item.get("originalPriceFormatted"))
            price = (cents / 100) if cents is not None else _price_number(formatted or price_text)
            return price_text or formatted, price, currency
    return price_text, _price_number(price_text), None


def _price_number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group(0)) if match else None


def _ref_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _resolved_items(value: Any, cache: dict[str, Any]) -> list[Any]:
    return [_resolve_ref(item, cache) for item in _ref_values(value)]


def _resolve_ref(value: Any, cache: dict[str, Any]) -> Any:
    ref = _ref_key(value)
    if ref:
        return cache.get(ref, value)
    return value


def _ref_key(value: Any) -> str | None:
    if isinstance(value, dict):
        return _string_value(value.get("__ref"))
    if isinstance(value, str) and ":" in value:
        return value
    return None


def _concept_id_from_url(value: str) -> str | None:
    match = re.search(r"/concept/(\d+)", value)
    return match.group(1) if match else None


def _release_date_text(value: Any) -> str | None:
    if isinstance(value, dict):
        return _string_value(value.get("value") or value.get("date"))
    return _string_value(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            items.append(text)
    return list(dict.fromkeys(items))


def _has_token(token: str, *values: Any) -> bool:
    normalized_token = token.lower().replace("-", "")
    for value in values:
        text = _string_value(value)
        if text and normalized_token in text.lower().replace("-", ""):
            return True
    return False


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


def _description(value: dict[str, Any], preferred_types: tuple[str, ...] = ("LONG", "SHORT")) -> str | None:
    descriptions = value.get("descriptions")
    if isinstance(descriptions, list):
        by_type: dict[str, str] = {}
        for item in descriptions:
            if not isinstance(item, dict):
                continue
            text = _clean_text(item.get("value"))
            kind = _string_value(item.get("type"))
            if text and kind:
                by_type[kind.upper()] = text
        for kind in preferred_types:
            if by_type.get(kind):
                return by_type[kind]

    for key in ("shortDescription", "description", "longDescription"):
        text = _clean_text(value.get(key))
        if text:
            return text
    return None


def _clean_text(value: Any) -> str | None:
    text = _string_value(value)
    if not text:
        return None
    return strip_tags(text) or None


def _company_names(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    names: list[str] = []
    for item in values:
        name = _clean_text(item)
        if name:
            names.append(" ".join(name.split()))
    return list(dict.fromkeys(names))


def _parse_ps_datetime(value: Any) -> str | None:
    if isinstance(value, dict):
        return _parse_ps_datetime(value.get("value") or value.get("date"))
    if isinstance(value, str) and "T" in value:
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return None


def _media_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                items.append(item)
    elif isinstance(value, dict):
        items.append(value)
    return items


def _media_url_by_role(value: Any, roles: list[str]) -> str | None:
    role_set = set(roles)
    for item in _media_items(value):
        role = _string_value(item.get("role"))
        url = _string_value(item.get("url"))
        if role in role_set and url and url.startswith("http"):
            return url
    return _first_media_url(value)


def _screenshot_urls(value: Any) -> list[str]:
    urls: list[str] = []
    for item in _media_items(value):
        role = (_string_value(item.get("role")) or "").upper()
        media_type = (_string_value(item.get("type")) or "").upper()
        url = _string_value(item.get("url"))
        if "SCREENSHOT" in role and media_type == "IMAGE" and url and url.startswith("http"):
            urls.append(url)
    return list(dict.fromkeys(urls))


def _video_media(value: Any) -> tuple[str, str | None] | None:
    for item in _media_items(value):
        media_type = (_string_value(item.get("type")) or "").upper()
        url = _string_value(item.get("url"))
        if media_type == "VIDEO" and url and url.startswith("http"):
            thumbnail = _first_media_url(item.get("thumbnail") or item.get("poster") or item.get("image"))
            return url, thumbnail
    return None


def _first_media_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, list):
        for item in value:
            url = _first_media_url(item)
            if url:
                return url
    if isinstance(value, dict):
        for key in ("url", "imageUrl", "thumbnailUrl"):
            url = _string_value(value.get(key))
            if url and url.startswith("http"):
                return url
        for item in value.values():
            url = _first_media_url(item)
            if url:
                return url
    return None


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find_key(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


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
