from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from html import unescape
from typing import Any
from urllib.parse import urlencode

from core.http_client import fetch_json, fetch_text
from core.models import CollectedEvent, CollectedGame, CollectorResult, StoreLink
from core.normalizers import parse_release_date, strip_tags


STEAM_COMING_SOON_BASE_URL = "https://store.steampowered.com/search/results/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=US&l=english"
STEAM_APP_PAGE_URL = "https://store.steampowered.com/app/{app_id}/?cc=US&l=english"
STEAM_PAGE_SIZE = 50

APP_ROW_RE = re.compile(
    r'<a[^>]+href="(?P<url>https://store\.steampowered\.com/app/(?P<app_id>\d+)/[^"]*)"[^>]*>'
    r"(?P<body>.*?)</a>",
    re.DOTALL,
)
TITLE_RE = re.compile(r'<span class="title">(?P<title>.*?)</span>', re.DOTALL)
DATE_RE = re.compile(
    r'<div class="[^"]*\bsearch_released\b[^"]*">(?P<date>.*?)</div>',
    re.DOTALL,
)
IMG_RE = re.compile(r'<img[^>]+(?:src|data-src)="(?P<src>[^"]+)"', re.DOTALL)
TAG_RE = re.compile(r'<span[^>]*>(?P<tag>.*?)</span>', re.DOTALL)
PRICE_BLOCK_RE = re.compile(
    r'<div[^>]+class="[^"]*\bsearch_price_discount_combined\b[^"]*"[^>]*data-price-final="(?P<cents>\d+)"[^>]*>'
    r"(?P<body>.*?)</div>\s*</div>",
    re.DOTALL,
)
FINAL_PRICE_RE = re.compile(r'<div[^>]+class="[^"]*\b(?:discount_final_price|search_price)\b[^"]*"[^>]*>(?P<price>.*?)</div>', re.DOTALL)
APP_PAGE_TAG_RE = re.compile(r'<a[^>]+class="[^"]*\bapp_tag\b[^"]*"[^>]*>(?P<tag>.*?)</a>', re.DOTALL)


def collect(limit: int, user_agent: str) -> CollectorResult:
    if limit <= 0:
        return CollectorResult(source_slug="steam", fetched_url=_search_url(0), raw_payload={"pages": []}, games=[])

    games: list[CollectedGame] = []
    seen_app_ids: set[int] = set()
    pages: list[dict[str, Any]] = []
    appdetails: dict[str, Any] = {}
    app_pages: dict[str, Any] = {}
    include_appdetails = _bool_env("STEAM_APPDETAILS_ENABLED", default=True)
    include_app_page_tags = _bool_env("STEAM_APP_PAGE_TAGS_ENABLED", default=True)
    start = 0

    while len(games) < limit:
        url = _search_url(start)
        payload = fetch_json(url, user_agent)
        pages.append({"url": url, "payload": payload})

        result = parse_search_payload(payload, url)
        if not result.games:
            break

        for game in result.games:
            app_id = game.external_ids.get("steamAppId")
            if not isinstance(app_id, int) or app_id in seen_app_ids:
                continue
            seen_app_ids.add(app_id)

            if include_appdetails:
                details_url = STEAM_APPDETAILS_URL.format(app_id=app_id)
                try:
                    details_payload = fetch_json(details_url, user_agent)
                    appdetails[str(app_id)] = {"url": details_url, "payload": details_payload}
                    game = apply_appdetails(game, details_payload)
                    if game is None:
                        continue
                except Exception as exc:
                    appdetails[str(app_id)] = {"url": details_url, "error": str(exc)}

            if include_app_page_tags:
                page_url = STEAM_APP_PAGE_URL.format(app_id=app_id)
                try:
                    page_html = fetch_text(page_url, user_agent)
                    tags = parse_app_page_tags(page_html)
                    app_pages[str(app_id)] = {"url": page_url, "tags": tags}
                    if tags:
                        game = apply_app_page_tags(game, tags)
                except Exception as exc:
                    app_pages[str(app_id)] = {"url": page_url, "error": str(exc)}

            games.append(game)
            if len(games) >= limit:
                break

        if not _should_fetch_next_page(payload, start, STEAM_PAGE_SIZE, len(result.games)):
            break
        start += STEAM_PAGE_SIZE

    raw_payload: dict[str, Any] = {"pages": pages}
    if appdetails:
        raw_payload["appdetails"] = appdetails
    if app_pages:
        raw_payload["appPages"] = app_pages

    return CollectorResult(
        source_slug="steam",
        fetched_url=pages[0]["url"] if pages else _search_url(0),
        raw_payload=raw_payload,
        games=games[:limit],
    )


def parse_search_payload(payload: Any, fetched_url: str) -> CollectorResult:
    html = ""
    if isinstance(payload, dict):
        html = str(payload.get("results_html") or payload.get("html") or "")
    elif isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            html = str(parsed.get("results_html") or parsed.get("html") or "")
        except json.JSONDecodeError:
            html = payload

    games: list[CollectedGame] = []
    for match in APP_ROW_RE.finditer(html):
        body = match.group("body")
        title_match = TITLE_RE.search(body)
        if not title_match:
            continue
        title = strip_tags(title_match.group("title"))
        date_match = DATE_RE.search(body)
        release_date, accuracy = parse_release_date(date_match.group("date") if date_match else None)
        img_match = IMG_RE.search(body)
        image_url = unescape(img_match.group("src")) if img_match else None
        source_url = unescape(match.group("url"))
        app_id = int(match.group("app_id"))

        price_info = _search_price_info(body)
        games.append(
            CollectedGame(
                title=title,
                source_slug="steam",
                source_url=source_url,
                platform_slugs=["pc", "steam"],
                release_date=release_date,
                date_accuracy=accuracy,
                cover_image_url=image_url,
                store_links=[
                    _steam_store_link(
                        app_id,
                        source_url,
                        price_info=price_info,
                        release_date_text=date_match.group("date") if date_match else None,
                    )
                ],
                external_ids={"steamAppId": app_id},
            )
        )

    return CollectorResult(
        source_slug="steam",
        fetched_url=fetched_url,
        raw_payload=payload,
        games=games,
    )


def apply_appdetails(game: CollectedGame, payload: Any) -> CollectedGame | None:
    app_id = game.external_ids.get("steamAppId")
    if not isinstance(app_id, int) or not isinstance(payload, dict):
        return game

    wrapper = payload.get(str(app_id))
    if not isinstance(wrapper, dict) or wrapper.get("success") is False:
        return game
    data = wrapper.get("data")
    if not isinstance(data, dict):
        return game
    if data.get("type") and str(data["type"]).lower() not in {"game", "demo"}:
        return None

    release_date = game.release_date
    date_accuracy = game.date_accuracy
    release_date_text = None
    release = data.get("release_date")
    if isinstance(release, dict):
        if release.get("coming_soon") is False:
            return None
        release_date_text = _string_value(release.get("date"))
        detail_date, detail_accuracy = parse_release_date(release.get("date"))
        release_date = detail_date or release_date
        date_accuracy = detail_accuracy if detail_date else date_accuracy

    external_ids = dict(game.external_ids)
    if isinstance(data.get("steam_appid"), int):
        external_ids["steamAppId"] = data["steam_appid"]

    price_info = _price_info(data)
    if price_info == (None, None, None) and game.store_links:
        existing_link = game.store_links[0]
        price_info = (existing_link.price_text, existing_link.price, existing_link.currency)

    demo_available = bool(data.get("demos")) if isinstance(data.get("demos"), list) else None
    store_link = _steam_store_link(
        app_id,
        game.source_url,
        price_info=price_info,
        demo_available=demo_available,
        release_date_text=release_date_text,
        metadata={
            "genres": _dict_description_list(data.get("genres")),
            "categories": _dict_description_list(data.get("categories")),
            "tags": _steam_tags(data),
            "languages": _supported_languages(data.get("supported_languages")),
            "supportsChinese": _supports_chinese(data.get("supported_languages")),
        },
    )

    return replace(
        game,
        title=str(data.get("name") or game.title),
        release_date=release_date,
        date_accuracy=date_accuracy,
        cover_image_url=data.get("header_image") or game.cover_image_url,
        description=data.get("short_description") or game.description,
        publishers=_string_list(data.get("publishers")),
        developers=_string_list(data.get("developers")),
        store_links=[store_link],
        events=_append_demo_event(game, demo_available, title=str(data.get("name") or game.title)),
        external_ids=external_ids,
    )


def _append_demo_event(game: CollectedGame, demo_available: bool | None, title: str) -> list[CollectedEvent]:
    events = list(game.events)
    if demo_available is not True:
        return events
    demo_event = CollectedEvent(
        event_type="demo",
        title=f"{title} demo",
        platform_slugs=["pc", "steam"],
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


def parse_app_page_tags(html: str) -> list[str]:
    tags: list[str] = []
    for match in APP_PAGE_TAG_RE.finditer(html):
        tag = " ".join(strip_tags(match.group("tag")).split())
        if tag and tag != "+":
            tags.append(tag)
    return list(dict.fromkeys(tags))


def apply_app_page_tags(game: CollectedGame, tags: list[str]) -> CollectedGame:
    if not tags or not game.store_links:
        return game
    store_link = game.store_links[0]
    metadata = dict(store_link.metadata or {})
    metadata["steamTags"] = tags
    metadata["tags"] = tags
    return replace(game, store_links=[replace(store_link, metadata=metadata)])


def _search_url(start: int, count: int = STEAM_PAGE_SIZE) -> str:
    params = {
        "query": "",
        "start": start,
        "count": count,
        "filter": "comingsoon",
        "infinite": 1,
        "force_infinite": 1,
        "sort_by": "Released_ASC",
        "category1": 998,
        "cc": "US",
        "l": "english",
    }
    return f"{STEAM_COMING_SOON_BASE_URL}?{urlencode(params)}"


def _should_fetch_next_page(payload: Any, start: int, count: int, parsed_count: int) -> bool:
    if parsed_count <= 0:
        return False
    total_count = _int_value(payload.get("total_count")) if isinstance(payload, dict) else None
    if total_count is not None and start + count >= total_count:
        return False
    return parsed_count >= count


def _int_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(strip_tags(item).split())
        if cleaned:
            items.append(cleaned)
    return list(dict.fromkeys(items))


def _steam_store_link(
    app_id: int,
    url: str,
    price_info: tuple[str | None, float | None, str | None] | None = None,
    demo_available: bool | None = None,
    release_date_text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoreLink:
    price_text, price, currency = price_info or (None, None, None)
    return StoreLink(
        id=f"steam:{app_id}",
        store_name="steam",
        url=url,
        platform_slugs=["pc", "steam"],
        product_id=str(app_id),
        edition_name="Steam store page",
        edition_type="STANDARD",
        price_text=price_text,
        price=price,
        currency=currency,
        demo_available=demo_available,
        release_date_text=strip_tags(release_date_text) if release_date_text else None,
        metadata=metadata or {},
    )


def _price_info(data: dict[str, Any]) -> tuple[str | None, float | None, str | None]:
    if data.get("is_free") is True:
        return "Free To Play", 0.0, None
    price = data.get("price_overview")
    if not isinstance(price, dict):
        return None, None, None
    final = _int_value(price.get("final"))
    formatted = _string_value(price.get("final_formatted") or price.get("initial_formatted"))
    currency = _string_value(price.get("currency"))
    return formatted, (final / 100) if final is not None else None, currency


def _search_price_info(body: str) -> tuple[str | None, float | None, str | None] | None:
    match = PRICE_BLOCK_RE.search(body)
    if not match:
        return None
    cents = _int_value(match.group("cents"))
    price_body = match.group("body")
    price_text = None
    price_match = FINAL_PRICE_RE.search(price_body)
    if price_match:
        price_text = " ".join(strip_tags(price_match.group("price")).split())
    visible_text = " ".join(strip_tags(price_body).split())
    if cents == 0:
        if re.search(r"\bfree\b", visible_text, re.IGNORECASE):
            return "Free To Play", 0.0, None
        return None
    if cents is None:
        return None
    return price_text or f"${cents / 100:.2f}", cents / 100, "USD"


def _steam_tags(data: dict[str, Any]) -> list[str]:
    genres = _dict_description_list(data.get("genres"))
    categories = _dict_description_list(data.get("categories"))
    return list(dict.fromkeys(genres + categories))


def _dict_description_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _string_value(item.get("description"))
        if text:
            items.append(" ".join(strip_tags(text).split()))
    return list(dict.fromkeys(items))


def _supported_languages(value: Any) -> list[str]:
    text = strip_tags(value) if isinstance(value, str) else ""
    if not text:
        return []
    text = re.sub(r"\*.*$", "", text)
    return [item.strip() for item in text.split(",") if item.strip()]


def _supports_chinese(value: Any) -> bool | None:
    languages = _supported_languages(value)
    if not languages:
        return None
    return any("chinese" in item.lower() for item in languages)


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
