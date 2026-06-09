from __future__ import annotations

import json
import re
from typing import Any

from core.http_client import fetch_text
from core.models import CollectedGame, CollectorResult
from core.normalizers import parse_release_date, strip_tags


NINTENDO_GAMES_URL = "https://www.nintendo.com/us/store/games/"
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
    html = fetch_text(NINTENDO_GAMES_URL, user_agent)
    return parse_store_page(html, NINTENDO_GAMES_URL, limit=limit)


def parse_store_page(html: str, fetched_url: str, limit: int = 20) -> CollectorResult:
    games = _parse_next_data(html)
    if not games:
        games = _parse_json_ld(html)
    if not games:
        games = _parse_product_links(html)

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
