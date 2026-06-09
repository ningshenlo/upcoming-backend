from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from core.http_client import fetch_json_post
from core.models import CollectedGame, CollectorResult


IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
PLATFORM_SLUG_MAP = {
    "pc-microsoft-windows": "pc",
    "nintendo-switch": "nintendo-switch",
    "nintendo-switch-2": "nintendo-switch-2",
    "playstation-5": "ps5",
    "xbox-series-x-s": "xbox-series",
}


def collect(limit: int, user_agent: str) -> CollectorResult:
    client_id = os.environ.get("IGDB_CLIENT_ID")
    access_token = os.environ.get("IGDB_ACCESS_TOKEN")
    if not client_id or not access_token:
        return CollectorResult(
            source_slug="igdb",
            fetched_url="igdb://discovery/skipped",
            raw_payload={
                "status": "skipped",
                "reason": "missing_credentials",
                "required": ["IGDB_CLIENT_ID", "IGDB_ACCESS_TOKEN"],
            },
            games=[],
        )

    now = int(time.time())
    query = f"""
fields name, slug, url, summary, first_release_date, cover.image_id, platforms.name, platforms.slug;
where first_release_date != null & first_release_date > {now};
sort first_release_date asc;
limit {limit};
""".strip()
    payload = fetch_json_post(
        IGDB_GAMES_URL,
        query,
        user_agent,
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}",
        },
    )
    return parse_games_payload(payload, IGDB_GAMES_URL)


def parse_games_payload(payload: Any, fetched_url: str) -> CollectorResult:
    games = []
    rows = payload if isinstance(payload, list) else []
    for item in rows:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        games.append(
            CollectedGame(
                title=str(item["name"]),
                source_slug="igdb",
                source_url=_source_url(item),
                platform_slugs=_platform_slugs(item.get("platforms")),
                release_date=_release_date(item.get("first_release_date")),
                date_accuracy="candidate",
                description=item.get("summary"),
                cover_image_url=_cover_url(item.get("cover")),
                external_ids={"igdbId": item.get("id")},
            )
        )

    return CollectorResult(
        source_slug="igdb",
        fetched_url=fetched_url,
        raw_payload=payload,
        games=games,
    )


def _release_date(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()


def _source_url(item: dict[str, Any]) -> str:
    if item.get("url"):
        return str(item["url"])
    if item.get("slug"):
        return f"https://www.igdb.com/games/{item['slug']}"
    return "https://www.igdb.com"


def _cover_url(value: Any) -> str | None:
    if not isinstance(value, dict) or not value.get("image_id"):
        return None
    return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{value['image_id']}.jpg"


def _platform_slugs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    slugs = []
    for item in value:
        if not isinstance(item, dict):
            continue
        mapped = PLATFORM_SLUG_MAP.get(str(item.get("slug") or "").lower())
        if mapped:
            slugs.append(mapped)
            continue
        name = str(item.get("name") or "").lower()
        if "switch 2" in name:
            slugs.append("nintendo-switch-2")
        elif "switch" in name:
            slugs.append("nintendo-switch")
        elif "playstation 5" in name or name == "ps5":
            slugs.append("ps5")
        elif "xbox series" in name:
            slugs.append("xbox-series")
        elif "windows" in name or name == "pc":
            slugs.append("pc")
    return list(dict.fromkeys(slugs))
