from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


EPIC_BROWSE_URL = "https://store.epicgames.com/en-US/browse"
EPIC_GRAPHQL_URL = "https://store.epicgames.com/graphql"
EPIC_PAGE_SIZE = 40
REACT_QUERY_MARKER = "window.__REACT_QUERY_INITIAL_QUERIES__"
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


@dataclass(frozen=True)
class EpicProbeResult:
    page_url: str
    paging: dict[str, Any]
    games: list[dict[str, Any]]
    challenge_detected: bool = False


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


def parse_epic_react_query_state(html: str) -> dict[str, Any]:
    marker_index = html.find(REACT_QUERY_MARKER)
    if marker_index < 0:
        return {}
    brace_index = html.find("{", marker_index)
    if brace_index < 0:
        return {}
    try:
        state, _ = json.JSONDecoder().raw_decode(html[brace_index:])
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def parse_epic_search_store(html: str, page_url: str, limit: int) -> EpicProbeResult:
    challenge_detected = _challenge_detected(html, page_url)
    state = parse_epic_react_query_state(html)
    queries = state.get("queries") if isinstance(state, dict) else None
    if not isinstance(queries, list):
        return EpicProbeResult(page_url=page_url, paging={}, games=[], challenge_detected=challenge_detected)

    search_store: dict[str, Any] | None = None
    for query in queries:
        if not isinstance(query, dict):
            continue
        data = (((query.get("state") or {}).get("data") or {}).get("Catalog") or {}).get("searchStore")
        if isinstance(data, dict) and isinstance(data.get("elements"), list):
            search_store = data
            break

    if not search_store:
        return EpicProbeResult(page_url=page_url, paging={}, games=[], challenge_detected=challenge_detected)

    games = [_game_from_offer(offer) for offer in search_store.get("elements") or [] if isinstance(offer, dict)]
    return EpicProbeResult(
        page_url=page_url,
        paging=search_store.get("paging") if isinstance(search_store.get("paging"), dict) else {},
        games=[game for game in games if game][:limit],
        challenge_detected=challenge_detected,
    )


def parse_epic_graphql_payload(payload: Any, page_url: str, limit: int) -> EpicProbeResult:
    search_store = (((payload or {}).get("data") or {}).get("Catalog") or {}).get("searchStore")
    if not isinstance(search_store, dict):
        return EpicProbeResult(page_url=page_url, paging={}, games=[])
    games = [_game_from_offer(offer) for offer in search_store.get("elements") or [] if isinstance(offer, dict)]
    return EpicProbeResult(
        page_url=page_url,
        paging=search_store.get("paging") if isinstance(search_store.get("paging"), dict) else {},
        games=[game for game in games if game][:limit],
    )


def _game_from_offer(offer: dict[str, Any]) -> dict[str, Any]:
    page_slug = _page_slug(offer)
    release_date = _release_date(offer)
    price = _price(offer)
    mapping = _product_home_mapping(offer)
    return _compact(
        {
            "title": _string_value(offer.get("title")),
            "releaseDate": release_date,
            "releaseDateTime": _string_value(offer.get("releaseDate") or offer.get("pcReleaseDate")),
            "sourceUrl": f"https://store.epicgames.com/en-US/p/{page_slug}" if page_slug else None,
            "offerId": _string_value(offer.get("id")),
            "namespace": _string_value(offer.get("namespace")),
            "productId": _string_value(mapping.get("productId") if mapping else None),
            "sandboxId": _string_value(mapping.get("sandboxId") if mapping else None),
            "pageSlug": page_slug,
            "productSlug": _string_value(offer.get("productSlug")),
            "urlSlug": _string_value(offer.get("urlSlug")),
            "developer": _string_value(offer.get("developerDisplayName")),
            "publisher": _string_value(offer.get("publisherDisplayName")),
            "prePurchase": offer.get("prePurchase") if isinstance(offer.get("prePurchase"), bool) else None,
            "priceText": price.get("priceText"),
            "price": price.get("price"),
            "currency": price.get("currency"),
            "imageUrl": _image_url(offer, "OfferImageWide") or _image_url(offer, "DieselStoreFrontWide"),
            "tallImageUrl": _image_url(offer, "OfferImageTall") or _image_url(offer, "Thumbnail"),
            "categories": _category_paths(offer),
            "tagIds": _tag_ids(offer),
            "approximateReleasePlan": offer.get("approximateReleasePlan")
            if isinstance(offer.get("approximateReleasePlan"), dict)
            else None,
        }
    )


def _page_slug(offer: dict[str, Any]) -> str | None:
    mapping = _product_home_mapping(offer)
    if mapping:
        slug = _string_value(mapping.get("pageSlug"))
        if slug:
            return slug
    slug = _mapping_slug(offer.get("offerMappings"))
    return slug or _string_value(offer.get("productSlug")) or _string_value(offer.get("urlSlug"))


def _product_home_mapping(offer: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("catalogNs",):
        namespace = offer.get(key)
        if isinstance(namespace, dict):
            mappings = namespace.get("mappings")
            if isinstance(mappings, list):
                for item in mappings:
                    if isinstance(item, dict) and item.get("pageType") == "productHome":
                        return item
                for item in mappings:
                    if isinstance(item, dict):
                        return item
    return None


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


def _release_date(offer: dict[str, Any]) -> str | None:
    value = _string_value(offer.get("releaseDate") or offer.get("pcReleaseDate"))
    if value and "T" in value:
        return value.split("T", 1)[0]
    return value


def _price(offer: dict[str, Any]) -> dict[str, Any]:
    price = offer.get("price")
    total = price.get("totalPrice") if isinstance(price, dict) else None
    if not isinstance(total, dict):
        return {}
    decimals = ((total.get("currencyInfo") or {}).get("decimals")) if isinstance(total.get("currencyInfo"), dict) else 2
    amount = total.get("discountPrice")
    normalized = None
    if isinstance(amount, (int, float)):
        normalized = float(amount) / (10 ** int(decimals or 2))
    fmt = total.get("fmtPrice") if isinstance(total.get("fmtPrice"), dict) else {}
    return _compact(
        {
            "priceText": _string_value(fmt.get("discountPrice") or fmt.get("originalPrice")),
            "price": normalized,
            "currency": _string_value(total.get("currencyCode")),
        }
    )


def _image_url(offer: dict[str, Any], image_type: str) -> str | None:
    images = offer.get("keyImages")
    if not isinstance(images, list):
        return None
    for image in images:
        if isinstance(image, dict) and image.get("type") == image_type:
            return _string_value(image.get("url"))
    return None


def _category_paths(offer: dict[str, Any]) -> list[str]:
    categories = offer.get("categories")
    if not isinstance(categories, list):
        return []
    return [path for path in (_string_value(item.get("path")) for item in categories if isinstance(item, dict)) if path]


def _tag_ids(offer: dict[str, Any]) -> list[str]:
    tags = offer.get("tags")
    if not isinstance(tags, list):
        return []
    return [tag_id for tag_id in (_string_value(item.get("id")) for item in tags if isinstance(item, dict)) if tag_id]


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _challenge_detected(html: str, page_url: str) -> bool:
    return (
        "__cf_chl_rt_tk" in page_url
        or "cf_challenge" in html
        or "challenge-platform" in html
        or "Just a moment" in html
    )


async def run_probe(args: argparse.Namespace) -> list[EpicProbeResult]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Python Playwright is required. Install with: pip install playwright") from exc

    backend = _resolve_backend(args.backend)
    results: list[EpicProbeResult] = []
    async with async_playwright() as playwright:
        if backend == "cloudflare":
            browser = await playwright.chromium.connect_over_cdp(
                _cloudflare_cdp_url(),
                headers={"Authorization": f"Bearer {_cloudflare_token()}"},
            )
        else:
            browser = await playwright.chromium.launch(headless=not args.headful)
        page = await browser.new_page()
        try:
            for page_index in range(args.pages):
                start = args.start + page_index * args.count
                url = epic_browse_url(start=start, count=args.count)
                await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                try:
                    await page.wait_for_function(
                        f"document.documentElement.outerHTML.includes('{REACT_QUERY_MARKER}')",
                        timeout=args.timeout_ms,
                    )
                except Exception:
                    pass
                html = await page.content()
                results.append(parse_epic_search_store(html, page.url, args.limit))
        finally:
            await browser.close()
    return results


def run_curl_probe(args: argparse.Namespace) -> list[EpicProbeResult]:
    try:
        from curl_cffi import requests
    except ImportError as exc:
        raise RuntimeError("curl_cffi is required. Install with: pip install curl_cffi") from exc

    results: list[EpicProbeResult] = []
    for page_index in range(args.pages):
        start = args.start + page_index * args.count
        variables = {
            "allowCountries": "US",
            "category": "games/edition/base",
            "comingSoon": True,
            "count": args.count,
            "country": "US",
            "locale": "en-US",
            "sortBy": "releaseDate",
            "sortDir": "ASC",
            "start": start,
            "withPrice": True,
        }
        response = requests.post(
            EPIC_GRAPHQL_URL,
            json={
                "operationName": "searchStoreQuery",
                "query": EPIC_SEARCH_STORE_QUERY,
                "variables": variables,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://store.epicgames.com",
                "Referer": epic_browse_url(start=start, count=args.count),
            },
            impersonate="chrome",
            timeout=args.timeout_ms / 1000,
        )
        response.raise_for_status()
        results.append(parse_epic_graphql_payload(response.json(), epic_browse_url(start=start, count=args.count), args.limit))
    return results


def _resolve_backend(value: str) -> str:
    if value != "auto":
        return value
    try:
        import curl_cffi  # noqa: F401

        return "curl"
    except ImportError:
        pass
    return "cloudflare" if _cloudflare_account_id() and _cloudflare_token() else "local"


def _cloudflare_cdp_url() -> str:
    account_id = _cloudflare_account_id()
    if not account_id:
        raise RuntimeError("CF_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID is required for Cloudflare Browser Run")
    return (
        "wss://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/browser-rendering/devtools/browser?keep_alive=600000"
    )


def _cloudflare_account_id() -> str | None:
    return os.environ.get("CF_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")


def _cloudflare_token() -> str | None:
    return (
        os.environ.get("CF_BROWSER_RENDERING_API_TOKEN")
        or os.environ.get("CF_API_TOKEN")
        or os.environ.get("CLOUDFLARE_API_TOKEN")
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="POC probe for Epic Games Store upcoming SSR data.")
    parser.add_argument("--backend", choices=["auto", "curl", "local", "cloudflare"], default="auto")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=EPIC_PAGE_SIZE)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--headful", action="store_true", help="Use a visible local browser window.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv or sys.argv[1:])
    backend = _resolve_backend(args.backend)
    if backend == "curl":
        results = run_curl_probe(args)
    else:
        results = asyncio.run(run_probe(args))
    payload = {
        "backend": backend,
        "pages": [
            {
                "pageUrl": result.page_url,
                "paging": result.paging,
                "gameCount": len(result.games),
                "challengeDetected": result.challenge_detected,
                "games": result.games,
            }
            for result in results
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if any(result.games for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
