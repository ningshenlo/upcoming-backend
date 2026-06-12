from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from core.config import Settings
from core.http_client import FetchError, fetch_json, fetch_json_post
from core.job_logger import finish_job, resolve_job_status, start_job
from core.neon import NeonStore

YOUTUBE_SOURCE_SLUG = "youtube"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
DATAFORSEO_YOUTUBE_TASK_POST_URL = "https://api.dataforseo.com/v3/serp/youtube/organic/task_post"
DATAFORSEO_YOUTUBE_TASK_SOURCE = "serp/youtube/organic/task_post"
YOUTUBE_BATCH_SIZE = 50
SEARCH_MAX_RESULTS = 10

POSITIVE_TITLE_TERMS = {
    "announcement",
    "cinematic",
    "gameplay",
    "launch",
    "official",
    "promo",
    "promotion",
    "reveal",
    "teaser",
    "trailer",
}
NEGATIVE_TITLE_TERMS = {
    "analysis",
    "fan",
    "lets play",
    "ost",
    "reaction",
    "review",
    "rumor",
    "soundtrack",
    "walkthrough",
}
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GameRow:
    id: str
    title: str
    aliases: list[str]
    trailer_url: str | None
    publishers: list[str]
    developers: list[str]


@dataclass(frozen=True)
class YouTubeCandidate:
    video_id: str
    title: str | None
    description: str | None
    channel_id: str | None
    channel_title: str | None
    published_at: str | None
    thumbnail_url: str | None
    video_url: str
    match_confidence: int
    match_reasons: list[str]


@dataclass(frozen=True)
class TrackedVideoRow:
    id: str
    game_id: str
    video_id: str


@dataclass(frozen=True)
class DiscoveryResult:
    created: bool
    status: str
    error_message: str | None = None


@dataclass(frozen=True)
class YouTubeRunResult:
    status: str
    processed_count: int
    failed_count: int


class YouTubeApiError(RuntimeError):
    pass


def run(
    settings: Settings,
    discover_limit: int,
    refresh_limit: int,
    rediscovery_days: int,
) -> YouTubeRunResult:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for hot tracker")
    if refresh_limit > 0 and not settings.youtube_api_key:
        raise RuntimeError("YOUTUBE_API_KEY is required for YouTube hot tracking")
    if discover_limit > 0 and not settings.dataforseo_api_key:
        raise RuntimeError("DFS or DATAFORSEO_API_KEY is required for YouTube discovery")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "youtube_track",
            {
                "channels": ["youtube"],
                "mode": "hot_tracker",
                "discoverLimit": discover_limit,
                "refreshLimit": refresh_limit,
                "rediscoveryDays": rediscovery_days,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        print(
            "youtube hot tracker: started "
            f"job_id={job.job_id} discover_limit={discover_limit} refresh_limit={refresh_limit} "
            f"rediscovery_days={rediscovery_days}",
            flush=True,
        )
        _ensure_youtube_source(store)

        discovery_rows = _discoverable_game_rows(store, discover_limit, rediscovery_days)
        print(f"youtube hot tracker: discovery candidates={len(discovery_rows)}", flush=True)
        for row in discovery_rows:
            try:
                job.processed_count += 1
                result = _discover_game(store, row, settings)
                if result.created:
                    job.created_count += 1
                else:
                    job.updated_count += 1
                if result.status == "error":
                    print(
                        "youtube hot tracker: discovery failed "
                        f"game_id={row.id} title={row.title!r} error={result.error_message}",
                        file=sys.stderr,
                        flush=True,
                    )
                    job.error_count += 1
                    job.errors.append(
                        {
                            "phase": "discovery",
                            "gameId": row.id,
                            "title": row.title,
                            "message": result.error_message or "YouTube discovery failed",
                        }
                    )
                store.conn.commit()
            except Exception as exc:
                job.error_count += 1
                job.errors.append({"gameId": row.id, "title": row.title, "message": str(exc)})
                try:
                    store.conn.rollback()
                except Exception:
                    store.reconnect()

        tracked_rows = _tracked_video_rows(store, refresh_limit)
        print(
            "youtube hot tracker: refresh candidates="
            f"{len(tracked_rows)} batch_size={YOUTUBE_BATCH_SIZE}",
            flush=True,
        )
        for chunk in _chunks(tracked_rows, YOUTUBE_BATCH_SIZE):
            if not chunk:
                continue
            try:
                print(f"youtube hot tracker: refreshing stats batch_size={len(chunk)}", flush=True)
                stats_by_id = fetch_youtube_video_stats(
                    [row.video_id for row in chunk],
                    api_key=settings.youtube_api_key,
                    user_agent=settings.user_agent,
                )
                print(
                    "youtube hot tracker: refreshed stats "
                    f"requested={len(chunk)} returned={len(stats_by_id)}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"youtube hot tracker: refresh batch failed size={len(chunk)} error={exc}",
                    file=sys.stderr,
                    flush=True,
                )
                job.error_count += len(chunk)
                job.errors.append({"phase": "refresh", "message": str(exc)})
                continue

            for row in chunk:
                try:
                    job.processed_count += 1
                    payload = stats_by_id.get(row.video_id)
                    if not payload:
                        print(
                            "youtube hot tracker: refresh skipped missing stats "
                            f"game_id={row.game_id} video_id={row.video_id}",
                            flush=True,
                        )
                        job.skipped_count += 1
                        continue
                    _record_video_metrics(store, row, payload, job.job_id)
                    job.updated_count += 1
                    store.conn.commit()
                except Exception as exc:
                    job.error_count += 1
                    job.errors.append({"videoId": row.video_id, "message": str(exc)})
                    try:
                        store.conn.rollback()
                    except Exception:
                        store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        if status == "failed":
            store.record_source_failure(YOUTUBE_SOURCE_SLUG, job.job_id, error_message or "YouTube hot tracker failed")
        else:
            store.record_source_success(YOUTUBE_SOURCE_SLUG)
        print(
            "youtube hot tracker: finished "
            f"job_id={job.job_id} status={status} processed={job.processed_count} "
            f"created={job.created_count} updated={job.updated_count} skipped={job.skipped_count} "
            f"errors={job.error_count}",
            flush=True,
        )
        return YouTubeRunResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def extract_youtube_video_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.split("/") if part]

    if host.endswith("youtu.be") and path_parts:
        return _clean_video_id(path_parts[0])
    if "youtube.com" not in host:
        return None

    query_video_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_video_id:
        return _clean_video_id(query_video_id)

    if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
        return _clean_video_id(path_parts[1])
    return None


def build_discovery_query(title: str) -> str:
    return f"{title} official trailer"


def dataforseo_youtube_task_source_for_game(game_id: str) -> str:
    return f"{DATAFORSEO_YOUTUBE_TASK_SOURCE}:game_id={game_id}"


def score_candidate(game: GameRow, candidate: YouTubeCandidate) -> YouTubeCandidate:
    title_text = _normalize_text(candidate.title)
    description_text = _normalize_text(candidate.description)
    channel_text = _normalize_text(candidate.channel_title)
    game_terms = [game.title, *game.aliases]
    company_terms = [*game.publishers, *game.developers]
    score = 0
    reasons: list[str] = []

    if _contains_any_term(title_text, game_terms):
        score += 45
        reasons.append("title_matches_game")
    elif _contains_any_term(description_text, game_terms):
        score += 20
        reasons.append("description_matches_game")
    else:
        score -= 20
        reasons.append("missing_game_title")

    positive_hits = _matching_words(title_text, POSITIVE_TITLE_TERMS)
    if positive_hits:
        score += min(25, len(positive_hits) * 8)
        reasons.append("positive_terms:" + ",".join(positive_hits))

    negative_hits = _matching_words(title_text, NEGATIVE_TITLE_TERMS)
    if negative_hits:
        score -= min(35, len(negative_hits) * 15)
        reasons.append("negative_terms:" + ",".join(negative_hits))

    if _contains_any_term(channel_text, company_terms):
        score += 25
        reasons.append("channel_matches_company")
    elif "official" in channel_text:
        score += 8
        reasons.append("channel_says_official")

    confidence = max(10, min(100, score))
    return YouTubeCandidate(
        video_id=candidate.video_id,
        title=candidate.title,
        description=candidate.description,
        channel_id=candidate.channel_id,
        channel_title=candidate.channel_title,
        published_at=candidate.published_at,
        thumbnail_url=candidate.thumbnail_url,
        video_url=candidate.video_url,
        match_confidence=confidence,
        match_reasons=reasons,
    )


def select_best_candidate(game: GameRow, candidates: list[YouTubeCandidate]) -> YouTubeCandidate | None:
    if not candidates:
        return None
    scored = [score_candidate(game, candidate) for candidate in candidates]
    return max(scored, key=lambda item: item.match_confidence)


def search_youtube_candidates(query: str, api_key: str, user_agent: str) -> list[YouTubeCandidate]:
    payload = _fetch_youtube_json(
        YOUTUBE_SEARCH_URL,
        {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": str(SEARCH_MAX_RESULTS),
            "order": "relevance",
            "key": api_key,
        },
        api_key=api_key,
        user_agent=user_agent,
    )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    candidates: list[YouTubeCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") if isinstance(item.get("id"), dict) else {}
        video_id = item_id.get("videoId")
        if not isinstance(video_id, str) or not video_id:
            continue
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        candidates.append(_candidate_from_snippet(video_id, snippet))
    return candidates


def candidate_from_dataforseo_item(item: dict[str, Any]) -> YouTubeCandidate | None:
    if item.get("type") != "youtube_video":
        return None
    if item.get("is_live") is True or item.get("is_shorts") is True:
        return None

    video_id = _string_value(item.get("video_id")) or extract_youtube_video_id(_string_value(item.get("url")))
    if not video_id:
        return None

    video_url = _string_value(item.get("url")) or YOUTUBE_WATCH_URL.format(video_id=video_id)
    return YouTubeCandidate(
        video_id=video_id,
        title=_string_value(item.get("title")),
        description=_string_value(item.get("description")),
        channel_id=_string_value(item.get("channel_id")),
        channel_title=_string_value(item.get("channel_name")),
        published_at=_string_value(item.get("timestamp")),
        thumbnail_url=_string_value(item.get("thumbnail_url")),
        video_url=video_url,
        match_confidence=0,
        match_reasons=[],
    )


def fetch_youtube_video_stats(video_ids: list[str], api_key: str, user_agent: str) -> dict[str, dict[str, Any]]:
    if not video_ids:
        return {}
    payload = _fetch_youtube_json(
        YOUTUBE_VIDEOS_URL,
        {
            "part": "snippet,statistics",
            "id": ",".join(video_ids),
            "key": api_key,
        },
        api_key=api_key,
        user_agent=user_agent,
    )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            result[item["id"]] = item
    return result


def _discover_game(store: NeonStore, row: GameRow, settings: Settings) -> DiscoveryResult:
    query = build_discovery_query(row.title)
    trailer_video_id = extract_youtube_video_id(row.trailer_url)
    if trailer_video_id:
        print(
            "youtube hot tracker: using existing trailer_url "
            f"game_id={row.id} title={row.title!r} video_id={trailer_video_id}",
            flush=True,
        )
        candidate = YouTubeCandidate(
            video_id=trailer_video_id,
            title=None,
            description=None,
            channel_id=None,
            channel_title=None,
            published_at=None,
            thumbnail_url=None,
            video_url=YOUTUBE_WATCH_URL.format(video_id=trailer_video_id),
            match_confidence=95,
            match_reasons=["games_trailer_url"],
        )
        created = _upsert_tracked_video(store, row, candidate, query, "found", {"source": "games.trailer_url"})
        return DiscoveryResult(created=created, status="found")

    try:
        print(
            "youtube hot tracker: submitting dfs discovery "
            f"game_id={row.id} title={row.title!r} query={query!r}",
            flush=True,
        )
        created = submit_dataforseo_youtube_discovery_task(store, row, query, settings)
    except Exception as exc:
        return DiscoveryResult(created=False, status="error", error_message=str(exc))
    return DiscoveryResult(created=created, status="submitted")


def submit_dataforseo_youtube_discovery_task(
    store: NeonStore,
    row: GameRow,
    query: str,
    settings: Settings,
) -> bool:
    source = dataforseo_youtube_task_source_for_game(row.id)
    task = {
        "keyword": query,
        "location_code": settings.dataforseo_youtube_location_code,
        "language_code": settings.dataforseo_youtube_language_code,
        "device": "desktop",
        "os": "windows",
        "block_depth": settings.dataforseo_youtube_block_depth,
        "priority": 1,
        "tag": source,
    }
    pingback_url = _dataforseo_pingback_url(settings.dataforseo_pingback_url)
    if pingback_url:
        task["pingback_url"] = pingback_url

    payload = _post_dataforseo_tasks([task], settings)
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    task_payload = tasks[0] if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else {}
    task_id = _string_value(task_payload.get("id"))
    task_status = task_payload.get("status_code")
    if not task_id:
        raise YouTubeApiError(f"DataForSEO task_post returned no task id: {payload.get('status_message')}")
    if isinstance(task_status, int) and task_status >= 40000:
        raise YouTubeApiError(f"DataForSEO task_post failed: {task_status} {task_payload.get('status_message')}")

    inserted = _insert_dfstask(store, task_id, source)
    print(
        "youtube hot tracker: dfs task submitted "
        f"game_id={row.id} task_id={task_id} inserted={inserted} "
        f"pingback_configured={bool(pingback_url)}",
        flush=True,
    )
    return inserted


def _post_dataforseo_tasks(tasks: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    api_key = settings.dataforseo_api_key or ""
    try:
        payload = fetch_json_post(
            DATAFORSEO_YOUTUBE_TASK_POST_URL,
            json.dumps(tasks, ensure_ascii=False),
            settings.user_agent,
            headers={
                "Authorization": _dataforseo_authorization_header(api_key),
                "Content-Type": "application/json",
            },
            timeout=60,
        )
    except FetchError as exc:
        raise YouTubeApiError(str(exc).replace(api_key, "[redacted]")) from exc

    if not isinstance(payload, dict):
        raise YouTubeApiError("DataForSEO task_post returned an invalid response")
    if payload.get("status_code") != 20000:
        raise YouTubeApiError(f"DataForSEO task_post failed: {payload.get('status_message')}")
    return payload


def _insert_dfstask(store: NeonStore, task_id: str, source: str) -> bool:
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dfstasks (task_id, source, status, created_at, updated_at)
            VALUES (%s, %s, 'submitted', now(), now())
            ON CONFLICT (task_id) DO NOTHING
            RETURNING task_id
            """,
            (task_id, source),
        )
        return cur.fetchone() is not None


def _dataforseo_pingback_url(value: str | None) -> str | None:
    if not value:
        return None
    pingback_url = value.strip()
    if not pingback_url:
        return None
    if "$id" in pingback_url or "$tag" in pingback_url:
        return pingback_url
    separator = "&" if "?" in pingback_url else "?"
    return f"{pingback_url}{separator}id=$id&tag=$tag"


def _dataforseo_authorization_header(api_key: str) -> str:
    value = api_key.strip()
    if value.lower().startswith("basic "):
        return value
    return f"Basic {value}"


def _ensure_youtube_source(store: NeonStore) -> None:
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (
                name, slug, source_type, url, trust_level, refresh_frequency, parser_type, parser_config
            )
            VALUES (
                'YouTube Data API', 'youtube', 'youtube', 'https://www.googleapis.com/youtube/v3',
                4, 'daily', 'youtube-data-api-v3', '{}'::jsonb
            )
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                source_type = EXCLUDED.source_type,
                url = EXCLUDED.url,
                trust_level = EXCLUDED.trust_level,
                refresh_frequency = EXCLUDED.refresh_frequency,
                parser_type = EXCLUDED.parser_type,
                parser_config = COALESCE(sources.parser_config, '{}'::jsonb) || EXCLUDED.parser_config,
                is_active = true,
                updated_at = now()
            """
        )


def _discoverable_game_rows(store: NeonStore, limit: int, rediscovery_days: int) -> list[GameRow]:
    if limit <= 0:
        return []
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            WITH company_names AS (
              SELECT
                gc.game_id,
                array_agg(DISTINCT c.name) FILTER (WHERE gc.role = 'publisher') AS publishers,
                array_agg(DISTINCT c.name) FILTER (WHERE gc.role = 'developer') AS developers
              FROM game_companies gc
              JOIN companies c ON c.id = gc.company_id
              GROUP BY gc.game_id
            )
            SELECT
              g.id,
              g.title,
              g.aliases,
              g.trailer_url,
              COALESCE(cn.publishers, '{}'::text[]) AS publishers,
              COALESCE(cn.developers, '{}'::text[]) AS developers
            FROM games g
            LEFT JOIN company_names cn ON cn.game_id = g.id
            LEFT JOIN youtube_tracked_videos ytv ON ytv.game_id = g.id
            LEFT JOIN dfstasks dfs
              ON dfs.source = %s || ':game_id=' || g.id
            WHERE ytv.id IS NULL
              AND dfs.task_id IS NULL
            ORDER BY
              EXISTS (
                SELECT 1
                FROM store_links sl
                WHERE sl.game_id = g.id
                  AND (sl.price IS NOT NULL OR NULLIF(BTRIM(sl.price_text), '') IS NOT NULL)
              ) DESC,
              COALESCE(ytv.last_discovered_at, g.last_scraped_at, g.updated_at, g.created_at) ASC NULLS FIRST,
              g.updated_at ASC
            LIMIT %s
            """,
            (DATAFORSEO_YOUTUBE_TASK_SOURCE, limit),
        )
        return [
            GameRow(
                id=row[0],
                title=row[1],
                aliases=list(row[2] or []),
                trailer_url=row[3],
                publishers=list(row[4] or []),
                developers=list(row[5] or []),
            )
            for row in cur.fetchall()
        ]


def _tracked_video_rows(store: NeonStore, limit: int) -> list[TrackedVideoRow]:
    if limit <= 0:
        return []
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, game_id, video_id
            FROM youtube_tracked_videos ytv
            WHERE ytv.is_active = true
              AND video_id IS NOT NULL
              AND (
                last_checked_at IS NULL
                OR last_checked_at::date < CURRENT_DATE
              )
            ORDER BY
              EXISTS (
                SELECT 1
                FROM store_links sl
                WHERE sl.game_id = ytv.game_id
                  AND (sl.price IS NOT NULL OR NULLIF(BTRIM(sl.price_text), '') IS NOT NULL)
              ) DESC,
              last_checked_at ASC NULLS FIRST,
              updated_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        return [TrackedVideoRow(id=row[0], game_id=row[1], video_id=row[2]) for row in cur.fetchall()]


def _upsert_tracked_video(
    store: NeonStore,
    row: GameRow,
    candidate: YouTubeCandidate | None,
    query: str,
    status: str,
    metadata: dict[str, Any],
) -> bool:
    assert store.conn is not None
    payload = _candidate_metadata(candidate) if candidate else {}
    with store.conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO youtube_tracked_videos (
                game_id, video_id, video_url, channel_id, channel_title, title, description,
                thumbnail_url, published_at, discovery_query, discovery_status, match_confidence,
                metadata, last_discovered_at, is_active
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), true
            )
            ON CONFLICT (game_id) DO UPDATE SET
                video_id = EXCLUDED.video_id,
                video_url = EXCLUDED.video_url,
                channel_id = EXCLUDED.channel_id,
                channel_title = EXCLUDED.channel_title,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                thumbnail_url = EXCLUDED.thumbnail_url,
                published_at = EXCLUDED.published_at,
                discovery_query = EXCLUDED.discovery_query,
                discovery_status = EXCLUDED.discovery_status,
                match_confidence = EXCLUDED.match_confidence,
                metadata = EXCLUDED.metadata,
                last_discovered_at = now(),
                is_active = true,
                updated_at = now()
            RETURNING (xmax = 0) AS inserted
            """,
            (
                row.id,
                candidate.video_id if candidate else None,
                candidate.video_url if candidate else None,
                candidate.channel_id if candidate else None,
                candidate.channel_title if candidate else None,
                candidate.title if candidate else None,
                candidate.description if candidate else None,
                candidate.thumbnail_url if candidate else None,
                _parse_datetime(candidate.published_at) if candidate else None,
                query,
                status,
                candidate.match_confidence if candidate else None,
                _json({"match": payload, **metadata}),
            ),
        )
        inserted = bool(cur.fetchone()[0])
    return inserted


def _record_video_metrics(
    store: NeonStore,
    row: TrackedVideoRow,
    payload: dict[str, Any],
    data_job_id: str | None,
) -> None:
    assert store.conn is not None
    snippet = payload.get("snippet") if isinstance(payload.get("snippet"), dict) else {}
    statistics = payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {}
    captured_date = datetime.now(timezone.utc).date().isoformat()
    view_count = _int_value(statistics.get("viewCount"))
    like_count = _int_value(statistics.get("likeCount"))
    comment_count = _int_value(statistics.get("commentCount"))
    video_url = YOUTUBE_WATCH_URL.format(video_id=row.video_id)
    thumbnail_url = _best_thumbnail_url(snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {})

    with store.conn.cursor() as cur:
        cur.execute(
            """
            UPDATE youtube_tracked_videos
            SET title = COALESCE(%s, title),
                description = COALESCE(%s, description),
                channel_id = COALESCE(%s, channel_id),
                channel_title = COALESCE(%s, channel_title),
                thumbnail_url = COALESCE(%s, thumbnail_url),
                published_at = COALESCE(%s, published_at),
                video_url = COALESCE(video_url, %s),
                last_checked_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (
                _string_value(snippet.get("title")),
                _string_value(snippet.get("description")),
                _string_value(snippet.get("channelId")),
                _string_value(snippet.get("channelTitle")),
                thumbnail_url,
                _parse_datetime(_string_value(snippet.get("publishedAt"))),
                video_url,
                row.id,
            ),
        )
        cur.execute(
            """
            INSERT INTO youtube_video_daily_metrics (
                tracked_video_id, game_id, video_id, captured_date,
                view_count, like_count, comment_count, data_job_id, raw_statistics, captured_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tracked_video_id, captured_date) DO UPDATE SET
                view_count = EXCLUDED.view_count,
                like_count = EXCLUDED.like_count,
                comment_count = EXCLUDED.comment_count,
                data_job_id = EXCLUDED.data_job_id,
                raw_statistics = EXCLUDED.raw_statistics,
                captured_at = now()
            """,
            (
                row.id,
                row.game_id,
                row.video_id,
                captured_date,
                view_count,
                like_count,
                comment_count,
                data_job_id,
                _json(statistics),
            ),
        )
        _insert_signal(cur, row.game_id, "youtube_views", view_count, video_url)
        _insert_signal(cur, row.game_id, "youtube_likes", like_count, video_url)
        _insert_signal(cur, row.game_id, "youtube_comments", comment_count, video_url)


def _insert_signal(cur, game_id: str, signal_type: str, value: int | None, source_url: str) -> None:
    if value is None:
        return
    cur.execute(
        """
        INSERT INTO signals (
            game_id, signal_type, value, time_window, source_id, source_url, observed_at
        )
        SELECT %s, %s, %s, '24h', s.id, %s, now()
        FROM sources s
        WHERE s.slug = %s
        """,
        (game_id, signal_type, float(value), source_url, YOUTUBE_SOURCE_SLUG),
    )


def _candidate_from_snippet(video_id: str, snippet: dict[str, Any]) -> YouTubeCandidate:
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    return YouTubeCandidate(
        video_id=video_id,
        title=_string_value(snippet.get("title")),
        description=_string_value(snippet.get("description")),
        channel_id=_string_value(snippet.get("channelId")),
        channel_title=_string_value(snippet.get("channelTitle")),
        published_at=_string_value(snippet.get("publishedAt")),
        thumbnail_url=_best_thumbnail_url(thumbnails),
        video_url=YOUTUBE_WATCH_URL.format(video_id=video_id),
        match_confidence=0,
        match_reasons=[],
    )


def _candidate_metadata(candidate: YouTubeCandidate | None) -> dict[str, Any]:
    if candidate is None:
        return {}
    return {
        "videoId": candidate.video_id,
        "videoUrl": candidate.video_url,
        "title": candidate.title,
        "channelId": candidate.channel_id,
        "channelTitle": candidate.channel_title,
        "publishedAt": candidate.published_at,
        "thumbnailUrl": candidate.thumbnail_url,
        "matchConfidence": candidate.match_confidence,
        "matchReasons": candidate.match_reasons,
    }


def _fetch_youtube_json(url: str, params: dict[str, str], api_key: str, user_agent: str) -> Any:
    full_url = f"{url}?{urlencode(params)}"
    try:
        return fetch_json(full_url, user_agent)
    except FetchError as exc:
        raise YouTubeApiError(str(exc).replace(api_key, "[redacted]")) from exc


def _contains_any_term(text: str, terms: list[str]) -> bool:
    return any(_normalize_text(term) and _normalize_text(term) in text for term in terms)


def _matching_words(text: str, words: set[str]) -> list[str]:
    return sorted(word for word in words if word in text)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return SPACE_RE.sub(" ", WORD_RE.sub(" ", value.lower())).strip()


def _clean_video_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_-].*$", "", value.strip())
    return cleaned or None


def _best_thumbnail_url(thumbnails: dict[str, Any]) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        item = thumbnails.get(key)
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            return item["url"]
    return None


def _parse_datetime(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
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


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _chunks(rows: list[TrackedVideoRow], size: int) -> list[list[TrackedVideoRow]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _json(value: Any) -> Any:
    try:
        from psycopg.types.json import Json
    except ImportError as exc:
        raise RuntimeError("psycopg JSON adapter is required for JSONB writes") from exc
    return Json(value)
