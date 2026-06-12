from __future__ import annotations

import json
import re
from dataclasses import replace
from urllib.parse import urljoin, urlparse
from typing import Any

from core.http_client import fetch_json_post, fetch_text
from core.models import CollectedEvent, CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, strip_tags


NINTENDO_BASE_URL = "https://www.nintendo.com"
NINTENDO_GAMES_URL = "https://www.nintendo.com/us/store/games/"
NINTENDO_COMING_SOON_URL = "https://www.nintendo.com/us/store/games/coming-soon/"
NINTENDO_ALGOLIA_APP_ID = "U3B6GR4UA3"
NINTENDO_ALGOLIA_API_KEY = "a29c6927638bfd8cee23993e51e721c9"
NINTENDO_ALGOLIA_INDEX = "store_game_en_us"
NINTENDO_ALGOLIA_PAGE_SIZE = 40
NINTENDO_ALGOLIA_QUERY_URL = (
    f"https://{NINTENDO_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{NINTENDO_ALGOLIA_INDEX}/query"
)
PRODUCT_LINK_RE = re.compile(
    r'href="(?P<url>/us/store/products/(?P<slug>[^"]+?)/?)"[^>]*>(?P<body>.{0,500}?)</a>',
    re.DOTALL,
)
ARIA_RE = re.compile(r'aria-label="(?P<label>[^"]+)"')
TITLE_ATTR_RE = re.compile(r'title="(?P<title>[^"]+)"')
JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(?P<body>.*?)</script>',
    re.DOTALL,
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(?P<body>.*?)</script>',
    re.DOTALL,
)


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(
            source_slug="nintendo",
            fetched_url=NINTENDO_ALGOLIA_QUERY_URL,
            raw_payload={"page": NINTENDO_COMING_SOON_URL, "endpoint": NINTENDO_ALGOLIA_QUERY_URL, "pages": []},
            games=[],
        )

    games: list[CollectedGame] = []
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 0

    while len(games) < limit:
        payload = _fetch_algolia_page(page, user_agent)
        pages.append({"page": page, "payload": payload})
        page_games = parse_algolia_payload(payload)
        if not page_games:
            break
        for game in page_games:
            key = _game_identity(game)
            if key in seen:
                continue
            seen.add(key)
            games.append(game)
            if len(games) >= limit:
                break
        if _is_last_algolia_page(payload, page):
            break
        page += 1

    return CollectorResult(
        source_slug="nintendo",
        fetched_url=NINTENDO_ALGOLIA_QUERY_URL,
        raw_payload={
            "source": "nintendo_algolia",
            "page": NINTENDO_COMING_SOON_URL,
            "endpoint": NINTENDO_ALGOLIA_QUERY_URL,
            "index": NINTENDO_ALGOLIA_INDEX,
            "request": _algolia_request_body(0, NINTENDO_ALGOLIA_PAGE_SIZE),
            "pages": pages,
        },
        games=games[:limit],
    )


def _fetch_algolia_page(page: int, user_agent: str) -> Any:
    body = json.dumps(_algolia_request_body(page, NINTENDO_ALGOLIA_PAGE_SIZE), separators=(",", ":"))
    return fetch_json_post(
        NINTENDO_ALGOLIA_QUERY_URL,
        body,
        user_agent,
        headers={
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": NINTENDO_ALGOLIA_APP_ID,
            "X-Algolia-API-Key": NINTENDO_ALGOLIA_API_KEY,
        },
    )


def _algolia_request_body(page: int, hits_per_page: int) -> dict[str, Any]:
    return {
        "query": "",
        "hitsPerPage": hits_per_page,
        "page": page,
        "distinct": True,
        "facets": ["*"],
        "attributesToHighlight": ["description"],
        "filters": 'availability:"Coming soon"',
    }


def parse_algolia_payload(payload: Any) -> list[CollectedGame]:
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    if not isinstance(hits, list):
        return []
    games: list[CollectedGame] = []
    seen: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        game = _game_from_store_product(hit, metadata_source="nintendo_algolia")
        if not game:
            continue
        key = _game_identity(game)
        if key in seen:
            continue
        seen.add(key)
        games.append(game)
    return games


def _is_last_algolia_page(payload: Any, page: int) -> bool:
    if not isinstance(payload, dict):
        return True
    nb_pages = _int_value(payload.get("nbPages"))
    if nb_pages is not None:
        return page + 1 >= nb_pages
    hits = payload.get("hits")
    return not isinstance(hits, list) or len(hits) < NINTENDO_ALGOLIA_PAGE_SIZE


def parse_store_page(html: str, fetched_url: str, limit: int = 20) -> CollectorResult:
    games = _parse_next_data(html)
    if not games:
        games = _parse_json_ld(html)
    if not games:
        games = _parse_product_links(html)
    games = _prioritize_fetched_product(games, fetched_url)

    return CollectorResult(
        source_slug="nintendo",
        fetched_url=fetched_url,
        raw_payload={"html": html},
        games=games[:limit],
    )


def _parse_next_data(html: str) -> list[CollectedGame]:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group("body").strip())
    except json.JSONDecodeError:
        return []

    product_games = _parse_next_data_products(data)
    if product_games:
        return product_games

    games: list[CollectedGame] = []
    seen_urls: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("__contentType") == "peonPromo":
                game = _game_from_peon_promo(value)
                if game and game.source_url not in seen_urls:
                    seen_urls.add(game.source_url)
                    games.append(game)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data.get("props", {}).get("pageProps", {}))
    return games


def _parse_next_data_products(data: dict[str, Any]) -> list[CollectedGame]:
    page_props = data.get("props", {}).get("pageProps", {})
    products: list[dict[str, Any]] = []
    grid = (
        page_props.get("page", {})
        .get("content", {})
        .get("merchandisedGrid")
    )
    if isinstance(grid, list):
        for section in grid:
            if isinstance(section, list):
                products.extend(item for item in section if isinstance(item, dict))

    apollo_state = page_props.get("initialApolloState")
    if isinstance(apollo_state, dict):
        for key, value in apollo_state.items():
            if key.startswith("Product:") and isinstance(value, dict):
                products.append(_resolve_apollo_refs(value, apollo_state))

    games: list[CollectedGame] = []
    seen: set[str] = set()
    for product in products:
        game = _game_from_store_product(product, metadata_source="nintendo_next_data")
        if not game:
            continue
        key = _game_identity(game)
        if key in seen:
            continue
        seen.add(key)
        games.append(game)
    return games


def _resolve_apollo_refs(value: Any, cache: dict[str, Any], depth: int = 0) -> Any:
    if depth > 4:
        return value
    if isinstance(value, dict):
        ref = _string_value(value.get("__ref"))
        if ref and isinstance(cache.get(ref), dict):
            return _resolve_apollo_refs(cache[ref], cache, depth + 1)
        return {key: _resolve_apollo_refs(item, cache, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_apollo_refs(item, cache, depth + 1) for item in value]
    return value


def _game_from_store_product(product: dict[str, Any], metadata_source: str) -> CollectedGame | None:
    title = _clean_text(product.get("title") or product.get("name") or product.get("productName"))
    if not title:
        return None

    source_url = _product_url(product)
    if not source_url:
        return None
    platform_slugs = _platform_slugs(product)
    release_date, accuracy = _parse_nintendo_release_date(product)
    cover_image_url = _cloudinary_url(product.get("productImage")) or _cloudinary_url(product.get("productImageSquare"))
    screenshots = _gallery_urls(product.get("productGallery"))
    store_link = _store_link_from_product(product, source_url, platform_slugs, metadata_source)
    external_ids: dict[str, Any] = {}
    nsuid = _string_value(product.get("nsuid"))
    sku = _string_value(product.get("sku") or product.get("objectID"))
    url_key = _string_value(product.get("urlKey"))
    if nsuid:
        external_ids["nintendoNsuid"] = nsuid
    if sku:
        external_ids["nintendoSku"] = sku
    if url_key:
        external_ids["nintendoUrlKey"] = url_key

    game = CollectedGame(
        title=title,
        source_slug="nintendo",
        source_url=source_url,
        platform_slugs=platform_slugs,
        release_date=release_date,
        date_accuracy=accuracy,
        launch_time_utc=None,
        description=_clean_text(product.get("description")),
        cover_image_url=cover_image_url,
        header_image_url=cover_image_url,
        screenshot_urls=screenshots,
        publishers=_company_names(product.get("softwarePublisher")),
        developers=_company_names(product.get("softwareDeveloper")),
        store_links=[store_link] if store_link else [],
        external_ids=external_ids,
    )
    return replace(game, events=_events_from_store_links(game, game.store_links))


def _store_link_from_product(
    product: dict[str, Any],
    source_url: str,
    platform_slugs: list[str],
    metadata_source: str,
) -> StoreLink | None:
    nsuid = _string_value(product.get("nsuid"))
    sku = _string_value(product.get("sku") or product.get("objectID"))
    url_key = _string_value(product.get("urlKey"))
    identity = nsuid or sku or url_key
    if not identity:
        return None
    price_text, price, currency = _price_info(product)
    availability = _string_list(product.get("availability"))
    top_filters = _string_list(product.get("topLevelFilters"))

    return StoreLink(
        id=f"nintendo_eshop:{identity}",
        store_name="nintendo_eshop",
        url=source_url,
        platform_slugs=platform_slugs,
        product_id=nsuid,
        sku_id=sku,
        edition_name=_clean_text(product.get("edition")),
        price_text=price_text,
        price=price,
        currency=currency,
        preorder_available=_bool_or_none(
            True if "Pre-order" in availability else None,
            _bool_value(_dict_value(product.get("eshopDetails")).get("isPreorderable") if _dict_value(product.get("eshopDetails")) else None),
            _bool_value(product.get("prePurchase")),
        ),
        demo_available=bool(_string_value(product.get("demoNsuid"))) or "Demo available" in top_filters,
        release_date_text=_string_value(product.get("releaseDateDisplay")) or _string_value(product.get("releaseDate")),
        metadata={
            "source": metadata_source,
            "sourceUrl": source_url,
            "urlKey": url_key,
            "availability": availability,
            "contentRating": _content_rating(product.get("contentRating")),
            "contentDescriptors": _content_descriptors(product.get("contentDescriptors")),
            "genres": _first_list(_string_list(product.get("gameGenreLabels")), _label_list(product.get("gameGenres"))),
            "tags": _first_list(_string_list(product.get("gameGenreLabels")), _label_list(product.get("gameGenres"))),
            "features": _first_list(_string_list(product.get("gameFeatureLabels")), _label_list(product.get("gameFeatures"))),
            "topLevelFilters": top_filters,
            "nsoFeatures": _label_list(product.get("nsoFeatures")),
            "waysToPlay": _first_list(_string_list(product.get("waysToPlayLabels")), _label_list(product.get("waysToPlay"))),
            "playerCount": _clean_text(product.get("playerCount") or product.get("playerCountDescription")),
            "demoNsuid": _string_value(product.get("demoNsuid")),
            "platform": _clean_text(product.get("platform")),
            "platformCode": _string_value(product.get("platformCode")),
            "stockStatus": _string_value(product.get("stockStatus")),
            "editions": _string_list(product.get("editions")),
            "hasDlc": _bool_value(product.get("hasDlc")),
            "isUpgrade": _bool_value(product.get("isUpgrade")),
            "exclusive": _bool_value(product.get("exclusive")),
            "price": _dict_value(product.get("price")),
            "eshopDetails": _dict_value(product.get("eshopDetails")),
            "productImage": _cloudinary_url(product.get("productImage")),
            "productGallery": _gallery_urls(product.get("productGallery")),
        },
    )


def _events_from_store_links(game: CollectedGame, store_links: list[StoreLink]) -> list[CollectedEvent]:
    if not any(link.demo_available is True for link in store_links):
        return []
    platform_slugs: list[str] = []
    for link in store_links:
        if link.demo_available is True:
            platform_slugs.extend(link.platform_slugs)
    return [
        CollectedEvent(
            event_type="demo",
            title=f"{game.title} demo",
            platform_slugs=list(dict.fromkeys(platform_slugs)) or game.platform_slugs,
            status="confirmed",
            confidence=85,
            source_url=game.source_url,
        )
    ]


def _game_identity(game: CollectedGame) -> str:
    for link in game.store_links:
        if link.id:
            return link.id
    return game.source_url


def _prioritize_fetched_product(games: list[CollectedGame], fetched_url: str) -> list[CollectedGame]:
    target = _product_path_key(fetched_url)
    if not target:
        return games
    for index, game in enumerate(games):
        if _product_path_key(game.source_url) == target:
            return [game, *games[:index], *games[index + 1 :]]
    return games


def _product_path_key(url: str | None) -> str | None:
    value = _string_value(url)
    if not value:
        return None
    path = urlparse(urljoin(NINTENDO_BASE_URL, value)).path.rstrip("/").lower()
    path = re.sub(r"^/us/", "/", path)
    if not path.startswith("/store/products/"):
        return None
    return path


def _game_from_peon_promo(node: dict[str, Any]) -> CollectedGame | None:
    cta = node.get("cta") or {}
    url = cta.get("url")
    if not isinstance(url, str) or "/store/products/" not in url:
        return None

    title, description = _rich_text_title_and_description(node.get("body"))
    if not title:
        title = _title_from_product_url(url)
    if not title or title.lower() in {"available now", "pre-order now", "coming soon"}:
        return None

    source_url = url if url.startswith("https://") else "https://www.nintendo.com" + url
    platform_slugs = ["nintendo-switch-2"] if "switch-2" in url.lower() else ["nintendo-switch"]
    image_url = _asset_image_url(node.get("asset"))

    return CollectedGame(
        title=title,
        source_slug="nintendo",
        source_url=source_url.rstrip("/"),
        platform_slugs=platform_slugs,
        release_date=None,
        date_accuracy="unknown",
        description=description,
        cover_image_url=image_url,
    )


def _rich_text_title_and_description(value: Any) -> tuple[str | None, str | None]:
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("nodeType") == "text" and node.get("value"):
                text = strip_tags(str(node["value"]))
                if text:
                    texts.append(text)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    if not texts:
        return None, None
    title = texts[0]
    description = " ".join(texts[1:]).strip() or None
    return title, description


def _title_from_product_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"-(switch|switch-2)$", "", slug)
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _asset_image_url(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    primary = value.get("primary")
    if isinstance(primary, dict):
        return primary.get("secure_url") or primary.get("url")
    return None


def _parse_json_ld(html: str) -> list[CollectedGame]:
    games: list[CollectedGame] = []
    for match in JSON_LD_RE.finditer(html):
        try:
            data = json.loads(match.group("body").strip())
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            games.extend(_games_from_json_node(node))
    return games


def _games_from_json_node(node: Any) -> list[CollectedGame]:
    if not isinstance(node, dict):
        return []

    graph = node.get("@graph")
    if isinstance(graph, list):
        games: list[CollectedGame] = []
        for child in graph:
            games.extend(_games_from_json_node(child))
        return games

    node_type = node.get("@type")
    if isinstance(node_type, list):
        is_product = "Product" in node_type or "VideoGame" in node_type
    else:
        is_product = node_type in {"Product", "VideoGame"}
    if not is_product:
        return []

    title = node.get("name")
    url = node.get("url") or NINTENDO_GAMES_URL
    if not title:
        return []

    release_date, accuracy = parse_release_date(
        node.get("releaseDate") or node.get("datePublished")
    )
    platforms = ["nintendo-switch"]
    text = json.dumps(node).lower()
    if "switch 2" in text or "nintendo switch 2" in text:
        platforms = ["nintendo-switch-2"]

    return [
        CollectedGame(
            title=strip_tags(str(title)),
            source_slug="nintendo",
            source_url=str(url),
            platform_slugs=platforms,
            release_date=release_date,
            date_accuracy=accuracy,
            description=node.get("description"),
            cover_image_url=_first_image(node.get("image")),
        )
    ]


def _parse_product_links(html: str) -> list[CollectedGame]:
    games: list[CollectedGame] = []
    seen: set[str] = set()
    for match in PRODUCT_LINK_RE.finditer(html):
        body = match.group("body")
        title = None
        aria = ARIA_RE.search(body)
        title_attr = TITLE_ATTR_RE.search(body)
        if aria:
            title = aria.group("label")
        elif title_attr:
            title = title_attr.group("title")
        else:
            title = strip_tags(body)
        title = _clean_title(title)
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())

        context = html[max(0, match.start() - 500) : min(len(html), match.end() + 500)]
        release_date, accuracy = parse_release_date(_find_date_nearby(context))
        platforms = ["nintendo-switch-2"] if "switch 2" in context.lower() else ["nintendo-switch"]
        url = "https://www.nintendo.com" + match.group("url").rstrip("/")
        games.append(
            CollectedGame(
                title=title,
                source_slug="nintendo",
                source_url=url,
                platform_slugs=platforms,
                release_date=release_date,
                date_accuracy=accuracy,
            )
        )
    return games


def _find_date_nearby(value: str) -> str | None:
    match = re.search(
        r"((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _clean_title(value: str | None) -> str | None:
    if not value:
        return None
    title = strip_tags(value)
    title = re.sub(r"\s+-\s+Nintendo.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+\|\s+.*$", "", title)
    return title.strip() or None


def _first_image(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, dict) and value.get("url"):
        return str(value["url"])
    return None


def _product_url(product: dict[str, Any]) -> str | None:
    url = _string_value(product.get("url"))
    if url:
        return urljoin(NINTENDO_BASE_URL, url).rstrip("/")
    url_key = _string_value(product.get("urlKey"))
    if url_key:
        return f"{NINTENDO_BASE_URL}/us/store/products/{url_key}/".rstrip("/")
    return None


def _platform_slugs(product: dict[str, Any]) -> list[str]:
    values: list[Any] = [
        product.get("platform"),
        product.get("platformCode"),
        product.get("fullNamePlatform"),
    ]
    for key in ("corePlatforms", "currentSystems", "categories"):
        value = product.get(key)
        if isinstance(value, list):
            values.extend(value)
    text = " ".join(str(item) for item in values if item)
    normalized = text.lower().replace("_", " ")
    if "switch 2" in normalized or "nintendo switch 2" in normalized:
        return ["nintendo-switch-2"]
    slugs: list[str] = []
    if "switch" in normalized or "nintendo switch" in normalized:
        slugs.append("nintendo-switch")
    return list(dict.fromkeys(slugs)) or ["nintendo-switch"]


def _parse_nintendo_release_date(product: dict[str, Any]) -> tuple[str | None, str]:
    display = _string_value(product.get("releaseDateDisplay"))
    if display:
        parsed, accuracy = parse_release_date(display)
        if parsed:
            return parsed, accuracy

    value = _string_value(product.get("releaseDate"))
    if value and "T" in value:
        parsed, accuracy = parse_release_date(value.split("T", 1)[0])
        if parsed:
            return parsed, accuracy
    return parse_release_date(value)


def _price_info(product: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    price_payload = _dict_value(product.get("price")) or {}
    eshop = _dict_value(product.get("eshopDetails")) or {}
    price = _float_value(
        price_payload.get("finalPrice"),
        price_payload.get("salePrice"),
        product.get("sortFinalPrice"),
        eshop.get("discountPrice"),
        eshop.get("regularPrice"),
    )
    if price is None:
        return None, None, None
    return f"${price:.2f}", price, "USD"


def _cloudinary_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("publicId") or value.get("assetPath") or value.get("url")
    text = _string_value(value)
    if not text:
        return None
    if text.startswith("http"):
        return text
    return f"https://assets.nintendo.com/image/upload/q_auto/f_auto/{text.lstrip('/')}"


def _gallery_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("resourceType") == "video":
            continue
        url = _cloudinary_url(item)
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _content_rating(value: Any) -> dict[str, Any]:
    rating = _dict_value(value)
    if not rating:
        return {}
    payload = {
        "system": _string_value(rating.get("system")),
        "code": _string_value(rating.get("code")),
        "label": _clean_text(rating.get("label")),
        "requiresAgeGate": _bool_value(rating.get("requiresAgeGate")),
    }
    return {key: item for key, item in payload.items() if item not in (None, "", [])}


def _content_descriptors(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    descriptors: list[str] = []
    for item in values:
        if isinstance(item, dict):
            text = _clean_text(item.get("label") or item.get("description"))
        else:
            text = _clean_text(item)
        if text:
            descriptors.append(text)
    return list(dict.fromkeys(descriptors))


def _label_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for item in values:
        if isinstance(item, dict):
            text = _clean_text(item.get("label") or item.get("name") or item.get("title"))
        else:
            text = _clean_text(item)
        if text:
            labels.append(text)
    return list(dict.fromkeys(labels))


def _first_list(*values: list[str]) -> list[str]:
    for value in values:
        if value:
            return value
    return []


def _company_names(value: Any) -> list[str]:
    return _label_list(value)


def _clean_text(value: Any) -> str | None:
    text = _string_value(value)
    if not text:
        return None
    return strip_tags(text) or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            items.append(text)
    return list(dict.fromkeys(items))


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
            return int(value)
        except ValueError:
            return None
    return None


def _float_value(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
            if match:
                return float(match.group(0))
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


def _bool_or_none(*values: bool | None) -> bool | None:
    for value in values:
        if value is not None:
            return value
    return None
