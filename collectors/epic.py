from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from core.models import CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, strip_tags


EPIC_BROWSE_URL = "https://store.epicgames.com/en-US/browse"
EPIC_GRAPHQL_URL = "https://store.epicgames.com/graphql"
EPIC_PAGE_SIZE = 40
EPIC_PLATFORM_SLUGS = ["pc", "epic-games-store"]
EPIC_SEARCH_STORE_QUERY = """query searchStoreQuery($allowCountries: String, $category: String, $comingSoon: Boolean, $count: Int, $country: String!, $locale: String, $sortBy: String, $sortDir: String, $start: Int, $withPrice: Boolean = false) {
  Catalog {
    searchStore(
      allowCountries: $allowCountries
      category: $category
      comingSoon: $comingSoon
      count: $count
      country: $country
      locale: $locale
      sortBy: $sortBy
      sortDir: $sortDir
      start: $start
    ) {
      elements {
        title
        id
        namespace
        description
        effectiveDate
        isCodeRedemptionOnly
        keyImages {
          type
          url
        }
        currentPrice
        seller {
          id
          name
        }
        productSlug
        urlSlug
        url
        tags {
          id
        }
        items {
          id
          namespace
        }
        customAttributes {
          key
          value
        }
        categories {
          path
        }
        catalogNs {
          mappings(pageType: "productHome") {
            pageSlug
            pageType
            productId
            sandboxId
          }
        }
        offerMappings {
          pageSlug
          pageType
        }
        developerDisplayName
        publisherDisplayName
        price(country: $country) @include(if: $withPrice) {
          totalPrice {
            discountPrice
            originalPrice
            currencyCode
            currencyInfo {
              decimals
            }
            fmtPrice(locale: $locale) {
              originalPrice
              discountPrice
              intermediatePrice
            }
          }
        }
        prePurchase
        releaseDate
        pcReleaseDate
        viewableDate
        approximateReleasePlan {
          day
          month
          quarter
          year
          releaseDateType
        }
      }
      paging {
        count
        total
      }
    }
  }
}"""
EPIC_CATALOG_OFFER_QUERY = """query catalogOfferQuery($namespace: String!, $id: String!, $country: String!, $locale: String, $withPrice: Boolean = true) {
  Catalog {
    catalogOffer(namespace: $namespace, id: $id, locale: $locale) {
      title
      id
      namespace
      description
      effectiveDate
      isCodeRedemptionOnly
      keyImages {
        type
        url
      }
      currentPrice
      seller {
        id
        name
      }
      productSlug
      urlSlug
      url
      tags {
        id
      }
      items {
        id
        namespace
      }
      customAttributes {
        key
        value
      }
      categories {
        path
      }
      catalogNs {
        mappings(pageType: "productHome") {
          pageSlug
          pageType
          productId
          sandboxId
        }
      }
      offerMappings {
        pageSlug
        pageType
      }
      developerDisplayName
      publisherDisplayName
      price(country: $country) @include(if: $withPrice) {
        totalPrice {
          discountPrice
          originalPrice
          currencyCode
          currencyInfo {
            decimals
          }
          fmtPrice(locale: $locale) {
            originalPrice
            discountPrice
            intermediatePrice
          }
        }
      }
      prePurchase
      releaseDate
      pcReleaseDate
      viewableDate
      approximateReleasePlan {
        day
        month
        quarter
        year
        releaseDateType
      }
    }
  }
}"""


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(
            source_slug="epic",
            fetched_url=epic_browse_url(0),
            raw_payload={"source": "epic_search_store", "endpoint": EPIC_GRAPHQL_URL, "pages": []},
            games=[],
        )

    games: list[CollectedGame] = []
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    start = 0

    while len(games) < limit:
        payload = fetch_search_page(start=start, count=EPIC_PAGE_SIZE, user_agent=user_agent)
        pages.append({"start": start, "payload": payload})
        page_games = parse_search_payload(payload)
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

        paging = _search_store_payload(payload).get("paging") or {}
        count = _int_value(paging.get("count")) or EPIC_PAGE_SIZE
        total = _int_value(paging.get("total"))
        start += count
        if count <= 0 or (total is not None and start >= total):
            break

    return CollectorResult(
        source_slug="epic",
        fetched_url=epic_browse_url(0),
        raw_payload={
            "source": "epic_search_store",
            "endpoint": EPIC_GRAPHQL_URL,
            "locale": "en-US",
            "country": "US",
            "pages": pages,
        },
        games=games[:limit],
    )


def epic_browse_url(start: int, count: int = EPIC_PAGE_SIZE) -> str:
    return EPIC_BROWSE_URL + "?" + urlencode(
        {
            "category": "Game",
            "count": count,
            "sortBy": "comingSoon",
            "sortDir": "ASC",
            "start": start,
        }
    )


def fetch_search_page(start: int, count: int, user_agent: str) -> Any:
    variables = {
        "allowCountries": "US",
        "category": "games/edition/base",
        "comingSoon": True,
        "count": count,
        "country": "US",
        "locale": "en-US",
        "sortBy": "releaseDate",
        "sortDir": "ASC",
        "start": start,
        "withPrice": True,
    }
    return _post_graphql(
        "searchStoreQuery",
        EPIC_SEARCH_STORE_QUERY,
        variables,
        referer=epic_browse_url(start, count),
        user_agent=user_agent,
    )


def fetch_offer_payload(namespace: str, offer_id: str, user_agent: str) -> Any:
    return _post_graphql(
        "catalogOfferQuery",
        EPIC_CATALOG_OFFER_QUERY,
        {
            "namespace": namespace,
            "id": offer_id,
            "country": "US",
            "locale": "en-US",
            "withPrice": True,
        },
        referer=EPIC_BROWSE_URL,
        user_agent=user_agent,
    )


def parse_search_payload(payload: Any) -> list[CollectedGame]:
    search_store = _search_store_payload(payload)
    return _games_from_offers(search_store.get("elements"), metadata_source="epic_search_store")


def parse_offer_payload(payload: Any) -> list[CollectedGame]:
    catalog_offer = (((payload or {}).get("data") or {}).get("Catalog") or {}).get("catalogOffer")
    return _games_from_offers([catalog_offer], metadata_source="epic_catalog_offer")


def _post_graphql(
    operation_name: str,
    query: str,
    variables: dict[str, Any],
    referer: str,
    user_agent: str,
) -> Any:
    try:
        from curl_cffi import requests
    except ImportError as exc:
        raise RuntimeError("curl_cffi is required for Epic Games Store collection") from exc

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://store.epicgames.com",
        "Referer": referer,
    }
    response = requests.post(
        EPIC_GRAPHQL_URL,
        json={"operationName": operation_name, "query": query, "variables": variables},
        headers=headers,
        impersonate="chrome",
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        raise RuntimeError(f"Epic GraphQL returned errors for {operation_name}: {errors}")
    return payload


def _search_store_payload(payload: Any) -> dict[str, Any]:
    search_store = (((payload or {}).get("data") or {}).get("Catalog") or {}).get("searchStore")
    return search_store if isinstance(search_store, dict) else {}


def _games_from_offers(value: Any, metadata_source: str) -> list[CollectedGame]:
    if not isinstance(value, list):
        return []
    games: list[CollectedGame] = []
    seen: set[str] = set()
    for offer in value:
        if not isinstance(offer, dict):
            continue
        game = _game_from_offer(offer, metadata_source)
        if not game:
            continue
        key = _game_identity(game)
        if key in seen:
            continue
        seen.add(key)
        games.append(game)
    return games


def _game_from_offer(offer: dict[str, Any], metadata_source: str) -> CollectedGame | None:
    offer_id = _string_value(offer.get("id"))
    namespace = _string_value(offer.get("namespace"))
    title = _clean_text(offer.get("title"))
    if not offer_id or not namespace or not title:
        return None

    mapping = _product_home_mapping(offer)
    page_slug = _page_slug(offer, mapping)
    source_url = _source_url(page_slug, offer)
    if not source_url:
        return None

    epic_product_id = _string_value(mapping.get("productId") if mapping else None)
    sandbox_id = _string_value(mapping.get("sandboxId") if mapping else None)
    product_id = epic_product_id or f"{namespace}:{offer_id}"
    release_date, date_accuracy = _release_date(offer)
    release_date_text = _string_value(offer.get("releaseDate") or offer.get("pcReleaseDate"))
    price_text, price, currency = _price_info(offer)
    tags = _tag_ids(offer)
    categories = _category_paths(offer)
    image_map = _image_map(offer)
    external_ids = {
        "epicOfferId": offer_id,
        "epicNamespace": namespace,
        "productId": product_id,
    }
    if epic_product_id:
        external_ids["epicProductId"] = epic_product_id

    store_link = StoreLink(
        id=f"epic_games_store:{product_id}",
        store_name="epic_games_store",
        url=source_url,
        platform_slugs=EPIC_PLATFORM_SLUGS,
        product_id=product_id,
        sku_id=offer_id,
        price_text=price_text,
        price=price,
        currency=currency,
        preorder_available=offer.get("prePurchase") if isinstance(offer.get("prePurchase"), bool) else None,
        wishlist_available=None,
        demo_available=None,
        release_date_text=release_date_text,
        metadata=_compact_metadata(
            {
                "source": metadata_source,
                "sourceUrl": source_url,
                "offerId": offer_id,
                "namespace": namespace,
                "productId": product_id,
                "epicProductId": epic_product_id,
                "sandboxId": sandbox_id,
                "pageSlug": page_slug,
                "productSlug": _string_value(offer.get("productSlug")),
                "urlSlug": _string_value(offer.get("urlSlug")),
                "categories": categories,
                "tags": tags,
                "seller": _seller(offer),
                "customAttributes": _custom_attributes(offer),
                "effectiveDate": _string_value(offer.get("effectiveDate")),
                "viewableDate": _string_value(offer.get("viewableDate")),
                "releaseDate": _string_value(offer.get("releaseDate")),
                "pcReleaseDate": _string_value(offer.get("pcReleaseDate")),
                "approximateReleasePlan": _dict_value(offer.get("approximateReleasePlan")),
                "isCodeRedemptionOnly": offer.get("isCodeRedemptionOnly")
                if isinstance(offer.get("isCodeRedemptionOnly"), bool)
                else None,
                "images": image_map,
            }
        ),
    )

    return CollectedGame(
        title=title,
        source_slug="epic",
        source_url=source_url,
        platform_slugs=EPIC_PLATFORM_SLUGS,
        release_date=release_date,
        date_accuracy=date_accuracy,
        launch_time_utc=_launch_time_utc(offer.get("releaseDate")),
        description=_clean_text(offer.get("description")),
        short_description=None,
        cover_image_url=_image_url(image_map, "OfferImageTall") or _image_url(image_map, "Thumbnail"),
        header_image_url=_image_url(image_map, "OfferImageWide") or _image_url(image_map, "DieselStoreFrontWide"),
        screenshot_urls=_screenshot_urls(image_map),
        trailer_url=None,
        trailer_thumbnail_url=None,
        publishers=_company_names(offer.get("publisherDisplayName")),
        developers=_company_names(offer.get("developerDisplayName")),
        store_links=[store_link],
        external_ids=external_ids,
    )


def _product_home_mapping(offer: dict[str, Any]) -> dict[str, Any] | None:
    namespace = offer.get("catalogNs")
    if not isinstance(namespace, dict):
        return None
    mappings = namespace.get("mappings")
    if not isinstance(mappings, list):
        return None
    for item in mappings:
        if isinstance(item, dict) and item.get("pageType") == "productHome":
            return item
    for item in mappings:
        if isinstance(item, dict):
            return item
    return None


def _page_slug(offer: dict[str, Any], mapping: dict[str, Any] | None) -> str | None:
    slug = _string_value(mapping.get("pageSlug") if mapping else None)
    if slug:
        return slug
    slug = _mapping_slug(offer.get("offerMappings"))
    return slug or _string_value(offer.get("productSlug")) or _string_value(offer.get("urlSlug"))


def _mapping_slug(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("pageType") == "productHome":
            slug = _string_value(item.get("pageSlug"))
            if slug:
                return slug
    for item in value:
        if isinstance(item, dict):
            slug = _string_value(item.get("pageSlug"))
            if slug:
                return slug
    return None


def _source_url(page_slug: str | None, offer: dict[str, Any]) -> str | None:
    if page_slug:
        return f"https://store.epicgames.com/en-US/p/{page_slug}"
    url = _string_value(offer.get("url"))
    if not url:
        return None
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return "https://store.epicgames.com" + url
    return None


def _release_date(offer: dict[str, Any]) -> tuple[str | None, str]:
    value = _string_value(offer.get("releaseDate")) or _string_value(offer.get("pcReleaseDate"))
    if value:
        return parse_release_date(value.split("T", 1)[0])

    plan = _dict_value(offer.get("approximateReleasePlan")) or {}
    year = _int_value(plan.get("year"))
    month = _int_value(plan.get("month"))
    quarter = _int_value(plan.get("quarter"))
    day = _int_value(plan.get("day"))
    if year and month and day:
        return f"{year:04d}-{month:02d}-{day:02d}", "exact"
    if year and month:
        return f"{year:04d}-{month:02d}-01", "month"
    if year and quarter:
        return f"{year:04d}-{((quarter - 1) * 3 + 1):02d}-01", "quarter"
    if year:
        return f"{year:04d}-01-01", "year"
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


def _price_info(offer: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    price = offer.get("price")
    total = price.get("totalPrice") if isinstance(price, dict) else None
    if not isinstance(total, dict):
        return None, None, None
    decimals = _int_value((total.get("currencyInfo") or {}).get("decimals")) if isinstance(total.get("currencyInfo"), dict) else 2
    amount = total.get("discountPrice")
    normalized = float(amount) / (10 ** int(decimals if decimals is not None else 2)) if isinstance(amount, (int, float)) else None
    fmt = total.get("fmtPrice") if isinstance(total.get("fmtPrice"), dict) else {}
    return (
        _string_value(fmt.get("discountPrice") or fmt.get("originalPrice")),
        normalized,
        _string_value(total.get("currencyCode")),
    )


def _image_map(offer: dict[str, Any]) -> dict[str, str]:
    images = offer.get("keyImages")
    if not isinstance(images, list):
        return {}
    result: dict[str, str] = {}
    for image in images:
        if not isinstance(image, dict):
            continue
        image_type = _string_value(image.get("type"))
        url = _string_value(image.get("url"))
        if image_type and url and url.startswith("http"):
            result[image_type] = url
    return result


def _image_url(images: dict[str, str], image_type: str) -> str | None:
    return images.get(image_type)


def _screenshot_urls(images: dict[str, str]) -> list[str]:
    urls: list[str] = []
    for image_type, url in images.items():
        lowered = image_type.lower()
        if "screenshot" in lowered or "carousel" in lowered:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _category_paths(offer: dict[str, Any]) -> list[str]:
    categories = offer.get("categories")
    if not isinstance(categories, list):
        return []
    paths = [_string_value(item.get("path")) for item in categories if isinstance(item, dict)]
    return [path for path in list(dict.fromkeys(paths)) if path]


def _tag_ids(offer: dict[str, Any]) -> list[str]:
    tags = offer.get("tags")
    if not isinstance(tags, list):
        return []
    values = [_string_value(item.get("id")) for item in tags if isinstance(item, dict)]
    return [value for value in list(dict.fromkeys(values)) if value]


def _seller(offer: dict[str, Any]) -> dict[str, str]:
    seller = _dict_value(offer.get("seller")) or {}
    return _compact_metadata({"id": _string_value(seller.get("id")), "name": _clean_text(seller.get("name"))})


def _custom_attributes(offer: dict[str, Any]) -> dict[str, str]:
    attributes = offer.get("customAttributes")
    if not isinstance(attributes, list):
        return {}
    result: dict[str, str] = {}
    for item in attributes:
        if not isinstance(item, dict):
            continue
        key = _string_value(item.get("key"))
        value = _string_value(item.get("value"))
        if key and value:
            result[key] = value
    return result


def _game_identity(game: CollectedGame) -> str:
    for link in game.store_links:
        if link.id:
            return link.id
    return game.source_url


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


def _compact_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
