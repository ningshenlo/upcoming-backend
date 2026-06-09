from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

from .models import CollectedEvent, CollectedGame, StoreLink
from .normalizers import slugify

GENERIC_GAME_SLUGS = {"demo", "beta", "alpha", "prologue", "playtest", "untitled"}
SUPPORTED_EVENT_TYPES = {
    "announcement",
    "trailer",
    "showcase",
    "demo",
    "beta",
    "early_access",
    "preload",
    "release",
    "dlc",
    "major_update",
    "delay",
    "delisting",
    "game_pass_addition",
    "ps_plus_addition",
    "subscription_removal",
}
DATE_ACCURACY_RANK = {
    "unknown": 0,
    "year": 1,
    "quarter": 2,
    "month": 3,
    "week": 4,
    "exact": 5,
}
EVENT_STATUSES = {"rumored", "leaked", "announced", "confirmed", "changed", "cancelled", "released"}
EVENT_REGIONS = {"global", "na", "eu", "jp", "asia", "other"}


def _json(value: Any) -> Any:
    if value is None:
        return None
    try:
        from psycopg.types.json import Json
    except ImportError as exc:
        raise RuntimeError("psycopg JSON adapter is required for JSONB writes") from exc
    return Json(value)


class NeonStore(AbstractContextManager["NeonStore"]):
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.conn = None
        self._has_source_observations_table: bool | None = None

    def __enter__(self) -> "NeonStore":
        self.conn = self._connect()
        return self

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required for Neon writes") from exc
        return psycopg.connect(self.database_url)

    def reconnect(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = self._connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is None:
            return
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()

    def create_data_job(self, job_type: str, params: dict[str, Any] | None = None) -> str:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_jobs (job_type, status, params, started_at)
                VALUES (%s, 'running', %s, clock_timestamp())
                RETURNING id
                """,
                (job_type, _json(params)),
            )
            job_id = cur.fetchone()[0]
        self.conn.commit()
        return job_id

    def finish_data_job(
        self,
        job_id: str,
        status: str,
        raw_data_path: str | None,
        processed_count: int,
        raw_data_paths: list[str] | None = None,
        created_count: int = 0,
        updated_count: int = 0,
        skipped_count: int = 0,
        error_count: int = 0,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        assert self.conn is not None
        metadata_payload = metadata or {"rawDataPaths": raw_data_paths or []}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE data_jobs
                SET status = %s,
                    raw_data_path = %s,
                    params = COALESCE(params, '{}'::jsonb) || %s::jsonb,
                    processed_count = %s,
                    created_count = %s,
                    updated_count = %s,
                    skipped_count = %s,
                    error_count = %s,
                    error_message = %s,
                    completed_at = job_finished.finished_at,
                    duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (job_finished.finished_at - started_at)) * 1000)::integer),
                    updated_at = job_finished.finished_at
                FROM (SELECT clock_timestamp() AS finished_at) AS job_finished
                WHERE id = %s
                """,
                (
                    status,
                    raw_data_path,
                    _json(metadata_payload),
                    processed_count,
                    created_count,
                    updated_count,
                    skipped_count,
                    error_count,
                    error_message,
                    job_id,
                ),
            )

    def archive_expired_upcoming_games(self) -> int:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                WITH first_release AS (
                  SELECT DISTINCT ON (game_id)
                    game_id,
                    date AS release_date
                  FROM release_events
                  WHERE event_type = 'release'
                    AND date_accuracy = 'exact'
                    AND date IS NOT NULL
                  ORDER BY game_id, date NULLS LAST, created_at
                ),
                expired AS (
                  SELECT g.id
                  FROM games g
                  JOIN first_release fr ON fr.game_id = g.id
                  WHERE g.status = 'upcoming'
                    AND fr.release_date::date < CURRENT_DATE
                )
                UPDATE games g
                SET status = 'released',
                    updated_at = now()
                FROM expired
                WHERE g.id = expired.id
                """
            )
            return cur.rowcount

    def fail_stale_running_jobs(self, timeout_hours: int = 2) -> int:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE data_jobs
                SET status = 'failed',
                    error_count = GREATEST(error_count, 1),
                    error_message = COALESCE(error_message, %s),
                    completed_at = clock_timestamp(),
                    duration_ms = GREATEST(
                      0,
                      (EXTRACT(EPOCH FROM (clock_timestamp() - COALESCE(started_at, created_at))) * 1000)::integer
                    ),
                    updated_at = clock_timestamp()
                WHERE status = 'running'
                  AND COALESCE(started_at, created_at) < NOW() - (%s * INTERVAL '1 hour')
                """,
                (f"Job timed out after {timeout_hours} hours.", timeout_hours),
            )
            return cur.rowcount

    def upsert_collected_game(
        self,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None = None,
    ) -> str:
        assert self.conn is not None
        game_slug = self._game_slug(item)
        needs_review = self._needs_review(item)
        data_completeness = self._data_completeness(item)
        with self.conn.cursor() as cur:
            existing_slug = self._existing_slug_for_collected_game(cur, item)
            if existing_slug:
                game_slug = existing_slug
            cur.execute(
                """
                INSERT INTO games (
                    slug, title, canonical_title, description, short_description,
                    cover_image_url, header_image_url, screenshot_urls,
                    trailer_url, trailer_thumbnail_url,
                    official_url, primary_release_date, steam_app_id, status,
                    data_completeness, needs_review, last_scraped_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'upcoming', %s, %s, now())
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title,
                    canonical_title = EXCLUDED.canonical_title,
                    description = COALESCE(EXCLUDED.description, games.description),
                    short_description = COALESCE(EXCLUDED.short_description, games.short_description),
                    cover_image_url = COALESCE(EXCLUDED.cover_image_url, games.cover_image_url),
                    header_image_url = COALESCE(EXCLUDED.header_image_url, games.header_image_url),
                    screenshot_urls = COALESCE(NULLIF(EXCLUDED.screenshot_urls, '{}'::text[]), games.screenshot_urls),
                    trailer_url = COALESCE(EXCLUDED.trailer_url, games.trailer_url),
                    trailer_thumbnail_url = COALESCE(EXCLUDED.trailer_thumbnail_url, games.trailer_thumbnail_url),
                    official_url = COALESCE(EXCLUDED.official_url, games.official_url),
                    primary_release_date = CASE
                        WHEN EXCLUDED.primary_release_date IS NULL THEN games.primary_release_date
                        WHEN games.primary_release_date IS NULL THEN EXCLUDED.primary_release_date
                        WHEN games.needs_review = true AND EXCLUDED.needs_review = false THEN EXCLUDED.primary_release_date
                        ELSE LEAST(games.primary_release_date, EXCLUDED.primary_release_date)
                    END,
                    steam_app_id = COALESCE(EXCLUDED.steam_app_id, games.steam_app_id),
                    data_completeness = GREATEST(
                        COALESCE(games.data_completeness, 0),
                        COALESCE(EXCLUDED.data_completeness, 0)
                    ),
                    needs_review = EXCLUDED.needs_review,
                    last_scraped_at = now(),
                    updated_at = now()
                RETURNING id
                """,
                (
                    game_slug,
                    item.title,
                    item.title.lower(),
                    item.description,
                    item.short_description,
                    item.cover_image_url,
                    item.header_image_url,
                    item.screenshot_urls,
                    item.trailer_url,
                    item.trailer_thumbnail_url,
                    item.source_url,
                    item.release_date,
                    item.external_ids.get("steamAppId"),
                    data_completeness,
                    needs_review,
                ),
            )
            game_id = cur.fetchone()[0]

            self._record_entity_observation(cur, game_id, item, raw_data_path, data_job_id)

            platform_ids = self._platform_ids(item.platform_slugs)
            for platform_id in platform_ids:
                cur.execute(
                    """
                    INSERT INTO game_platforms (game_id, platform_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (game_id, platform_id),
                )

            for event in self._events_for_item(item):
                event_id = self._upsert_game_event(cur, game_id, item, event, platform_ids, raw_data_path, data_job_id)
                if event_id:
                    self._record_event_observation(cur, game_id, event_id, item, event, raw_data_path, data_job_id)

            self._upsert_game_companies(cur, game_id, item)

            for store_link in item.store_links:
                self._upsert_store_link(cur, game_id, platform_ids, store_link, item, raw_data_path, data_job_id)

        return game_id

    def _needs_review(self, item: CollectedGame) -> bool:
        return (
            not item.release_date
            or item.date_accuracy != "exact"
            or not item.source_url
            or not item.platform_slugs
        )

    def _existing_slug_for_collected_game(self, cur, item: CollectedGame) -> str | None:
        steam_app_id = item.external_ids.get("steamAppId")
        if steam_app_id:
            cur.execute("SELECT slug FROM games WHERE steam_app_id = %s LIMIT 1", (steam_app_id,))
            row = cur.fetchone()
            if row:
                return row[0]

        link_ids = self._store_link_identity_ids(item)
        if link_ids:
            cur.execute(
                """
                SELECT g.slug
                FROM store_links sl
                JOIN games g ON g.id = sl.game_id
                WHERE sl.id = ANY(%s)
                LIMIT 1
                """,
                (link_ids,),
            )
            row = cur.fetchone()
            if row:
                return row[0]

        for link in item.store_links:
            if not link.store_name or not link.product_id:
                continue
            cur.execute(
                """
                SELECT g.slug
                FROM store_links sl
                JOIN games g ON g.id = sl.game_id
                WHERE sl.store_name = %s
                  AND sl.product_id = %s
                LIMIT 1
                """,
                (link.store_name, link.product_id),
            )
            row = cur.fetchone()
            if row:
                return row[0]

        return None

    def _store_link_identity_ids(self, item: CollectedGame) -> list[str]:
        ids: list[str] = []
        for link in item.store_links:
            if link.id:
                ids.append(link.id)
            elif link.store_name and link.url:
                ids.append(f"{link.store_name}:{link.product_id or slugify(link.url)}")
        return list(dict.fromkeys(ids))

    def _data_completeness(self, item: CollectedGame) -> float:
        score = 0
        score += 20 if item.release_date and item.date_accuracy == "exact" else 10 if item.release_date else 0
        score += 15 if item.platform_slugs else 0
        score += 10 if item.source_url else 0
        score += 10 if item.cover_image_url or item.header_image_url else 0
        score += 10 if item.description or item.short_description else 0
        score += 10 if item.publishers or item.developers else 0
        score += 15 if item.store_links else 0
        score += 10 if item.events or item.launch_time_utc else 0
        return min(1.0, score / 100)

    def _source_observations_available(self, cur) -> bool:
        if self._has_source_observations_table is None:
            cur.execute("SELECT to_regclass('public.source_observations') IS NOT NULL")
            self._has_source_observations_table = bool(cur.fetchone()[0])
        return self._has_source_observations_table

    def _insert_source_observation(
        self,
        cur,
        game_id: str,
        source_slug: str,
        fact_type: str,
        fact_key: str,
        observed_value: dict[str, Any],
        normalized_value: dict[str, Any],
        confidence: int,
        source_url: str | None = None,
        raw_data_path: str | None = None,
        data_job_id: str | None = None,
        release_event_id: str | None = None,
        store_link_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._source_observations_available(cur):
            return
        source_id = self._source_id(source_slug)
        cur.execute(
            """
            INSERT INTO source_observations (
                game_id, release_event_id, store_link_id, source_id, data_job_id,
                source_slug, source_url, raw_data_path, fact_type, fact_key,
                observed_value, normalized_value, confidence, metadata, observed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                game_id,
                release_event_id,
                store_link_id,
                source_id,
                data_job_id,
                source_slug,
                source_url,
                raw_data_path,
                fact_type,
                fact_key,
                _json(observed_value),
                _json(normalized_value),
                self._clamp_confidence(confidence),
                _json(metadata or {}),
            ),
        )

    def _record_entity_observation(
        self,
        cur,
        game_id: str,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None,
    ) -> None:
        self._insert_source_observation(
            cur,
            game_id=game_id,
            source_slug=item.source_slug,
            source_url=item.source_url,
            raw_data_path=raw_data_path,
            data_job_id=data_job_id,
            fact_type="entity_profile",
            fact_key="entity_profile",
            observed_value={
                "title": item.title,
                "description": item.description,
                "shortDescription": item.short_description,
                "coverImageUrl": item.cover_image_url,
                "headerImageUrl": item.header_image_url,
                "externalIds": item.external_ids,
            },
            normalized_value={
                "title": item.title,
                "canonicalTitle": item.title.lower(),
                "platformSlugs": item.platform_slugs,
                "publishers": item.publishers,
                "developers": item.developers,
            },
            confidence=85,
            metadata={"source": "collector_game"},
        )

    def _record_event_observation(
        self,
        cur,
        game_id: str,
        release_event_id: str,
        item: CollectedGame,
        event: CollectedEvent,
        raw_data_path: str | None,
        data_job_id: str | None,
    ) -> None:
        platform_slugs = event.platform_slugs or item.platform_slugs
        fact_type = self._event_fact_type(event)
        fact_key = f"{event.event_type}:{','.join(platform_slugs) or 'global'}"
        source_url = event.source_url or item.source_url
        observed_value = {
            "eventType": event.event_type,
            "title": event.title,
            "date": event.date,
            "dateAccuracy": event.date_accuracy,
            "launchTimeUtc": event.launch_time_utc,
            "platformSlugs": platform_slugs,
            "region": event.region,
            "status": event.status,
        }
        normalized_value = {
            "eventType": event.event_type,
            "date": event.date,
            "dateAccuracy": self._date_accuracy(event.date_accuracy),
            "launchTimeUtc": event.launch_time_utc,
            "platformSlugs": platform_slugs,
            "region": event.region if event.region in EVENT_REGIONS else "global",
        }
        self._insert_source_observation(
            cur,
            game_id=game_id,
            release_event_id=release_event_id,
            source_slug=item.source_slug,
            source_url=source_url,
            raw_data_path=raw_data_path,
            data_job_id=data_job_id,
            fact_type=fact_type,
            fact_key=fact_key,
            observed_value=observed_value,
            normalized_value=normalized_value,
            confidence=event.confidence,
            metadata={"source": "collector_event"},
        )
        if event.launch_time_utc:
            self._insert_source_observation(
                cur,
                game_id=game_id,
                release_event_id=release_event_id,
                source_slug=item.source_slug,
                source_url=source_url,
                raw_data_path=raw_data_path,
                data_job_id=data_job_id,
                fact_type="launch_time",
                fact_key=f"launch_time:{','.join(platform_slugs) or 'global'}",
                observed_value=observed_value,
                normalized_value=normalized_value,
                confidence=event.confidence,
                metadata={"source": "collector_event"},
            )

    def _event_fact_type(self, event: CollectedEvent) -> str:
        if event.event_type == "release":
            return "release_date"
        if event.event_type == "demo":
            return "demo_availability"
        return "event"

    def upsert_discovery_candidate(
        self,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None = None,
    ) -> str:
        assert self.conn is not None
        game_slug = self._game_slug(item)
        igdb_id = item.external_ids.get("igdbId")
        with self.conn.cursor() as cur:
            if igdb_id:
                cur.execute("SELECT id FROM games WHERE igdb_id = %s OR slug = %s LIMIT 1", (igdb_id, game_slug))
            else:
                cur.execute("SELECT id FROM games WHERE slug = %s LIMIT 1", (game_slug,))
            existing = cur.fetchone()

            if existing:
                game_id = existing[0]
                cur.execute(
                    """
                    UPDATE games
                    SET title = COALESCE(title, %s),
                        canonical_title = COALESCE(canonical_title, %s),
                        description = COALESCE(description, %s),
                        cover_image_url = COALESCE(cover_image_url, %s),
                        igdb_id = COALESCE(igdb_id, %s),
                        needs_review = true,
                        last_scraped_at = now(),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        item.title,
                        item.title.lower(),
                        item.description,
                        item.cover_image_url,
                        igdb_id,
                        game_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO games (
                        slug, title, canonical_title, description, cover_image_url,
                        igdb_id, status, needs_review, last_scraped_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'announced', true, now())
                    RETURNING id
                    """,
                    (
                        game_slug,
                        item.title,
                        item.title.lower(),
                        item.description,
                        item.cover_image_url,
                        igdb_id,
                    ),
                )
                game_id = cur.fetchone()[0]

            for platform_id in self._platform_ids(item.platform_slugs):
                cur.execute(
                    """
                    INSERT INTO game_platforms (game_id, platform_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (game_id, platform_id),
                )

        return game_id

    def _game_slug(self, item: CollectedGame) -> str:
        slug = slugify(item.title)
        steam_app_id = item.external_ids.get("steamAppId")
        source_identity = self._source_identity_slug(item)
        if steam_app_id and (slug in GENERIC_GAME_SLUGS or len(slug) <= 2):
            return f"steam-{steam_app_id}-{slug}"
        if source_identity and (slug in GENERIC_GAME_SLUGS or len(slug) <= 2):
            return f"{item.source_slug}-{source_identity}-{slug}"
        if slug != "untitled":
            return slug
        if steam_app_id:
            return f"steam-{steam_app_id}"
        if source_identity:
            return f"{item.source_slug}-{source_identity}"
        return f"{item.source_slug}-untitled"

    def _source_identity_slug(self, item: CollectedGame) -> str | None:
        for key in ("productId", "playstationProductId", "playstationConceptId", "npTitleId", "igdbId"):
            value = item.external_ids.get(key)
            if value:
                return slugify(str(value))
        return None

    def _platform_ids(self, platform_slugs: list[str]) -> list[str]:
        assert self.conn is not None
        if not platform_slugs:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM platforms WHERE slug = ANY(%s)",
                (platform_slugs,),
            )
            return [row[0] for row in cur.fetchall()]

    def _source_id(self, source_slug: str) -> str | None:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM sources WHERE slug = %s", (source_slug,))
            row = cur.fetchone()
            return row[0] if row else None

    def _upsert_game_companies(self, cur, game_id: str, item: CollectedGame) -> None:
        credits = [
            ("publisher", name)
            for name in self._company_names(item.publishers)
        ] + [
            ("developer", name)
            for name in self._company_names(item.developers)
        ]
        for role, name in credits:
            company_id = self._upsert_company(cur, name)
            cur.execute(
                """
                INSERT INTO game_companies (game_id, company_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (game_id, company_id, role),
            )

    def _upsert_company(self, cur, name: str) -> str:
        company_slug = slugify(name)
        cur.execute(
            """
            INSERT INTO companies (slug, name)
            VALUES (%s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                updated_at = now()
            RETURNING id
            """,
            (company_slug, name),
        )
        return cur.fetchone()[0]

    def _company_names(self, names: list[str]) -> list[str]:
        cleaned: list[str] = []
        for name in names:
            if not isinstance(name, str):
                continue
            value = " ".join(name.split())
            if value:
                cleaned.append(value)
        return list(dict.fromkeys(cleaned))

    def _events_for_item(self, item: CollectedGame) -> list[CollectedEvent]:
        events: list[CollectedEvent] = []
        if item.release_date:
            events.append(
                CollectedEvent(
                    event_type="release",
                    title=f"{item.title} release",
                    date=item.release_date,
                    date_accuracy=item.date_accuracy,
                    launch_time_utc=item.launch_time_utc,
                    platform_slugs=item.platform_slugs,
                    region="global",
                    status="confirmed",
                    confidence=90,
                    source_url=item.source_url,
                )
            )
        events.extend(item.events)

        deduped: list[CollectedEvent] = []
        seen: set[tuple] = set()
        for event in events:
            key = (
                event.event_type,
                tuple(event.platform_slugs or item.platform_slugs),
                event.date,
                event.launch_time_utc,
                event.source_url or item.source_url,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def _upsert_game_event(
        self,
        cur,
        game_id: str,
        item: CollectedGame,
        event: CollectedEvent,
        default_platform_ids: list[str],
        raw_data_path: str | None,
        data_job_id: str | None = None,
    ) -> str | None:
        if event.event_type not in SUPPORTED_EVENT_TYPES:
            return None

        platform_ids = self._platform_ids(event.platform_slugs) if event.platform_slugs else default_platform_ids
        source_id = self._source_id(item.source_slug)
        source_ids = [source_id] if source_id else []
        source_url = event.source_url or item.source_url
        source_urls = [source_url] if source_url else []
        date_accuracy = self._date_accuracy(event.date_accuracy)
        status = event.status if event.status in EVENT_STATUSES else "confirmed"
        region = event.region if event.region in EVENT_REGIONS else "global"
        confidence = self._clamp_confidence(event.confidence)

        if platform_ids:
            cur.execute(
                """
                SELECT id, date, date_accuracy, source_urls
                FROM release_events
                WHERE game_id = %s
                  AND event_type = %s
                  AND COALESCE(platform_ids, '{}'::text[]) && %s::text[]
                ORDER BY CASE WHEN date IS NOT DISTINCT FROM %s::timestamptz THEN 0 ELSE 1 END,
                         created_at ASC
                LIMIT 1
                """,
                (game_id, event.event_type, platform_ids, event.date),
            )
        else:
            cur.execute(
                """
                SELECT id, date, date_accuracy, source_urls
                FROM release_events
                WHERE game_id = %s
                  AND event_type = %s
                  AND COALESCE(platform_ids, '{}'::text[]) = '{}'::text[]
                ORDER BY CASE WHEN date IS NOT DISTINCT FROM %s::timestamptz THEN 0 ELSE 1 END,
                         created_at ASC
                LIMIT 1
                """,
                (game_id, event.event_type, event.date),
            )
        existing = cur.fetchone()
        new_date = event.date

        if event.event_type == "release" and existing and existing[1] and new_date:
            existing_date = existing[1].date().isoformat()
            existing_accuracy = self._date_accuracy(existing[2])
            incoming_is_more_precise = DATE_ACCURACY_RANK[date_accuracy] > DATE_ACCURACY_RANK[existing_accuracy]
            if existing_date != new_date and not incoming_is_more_precise:
                self.create_date_conflict_alert(
                    game_id=game_id,
                    release_event_id=existing[0],
                    source_id=source_id,
                    existing_date=existing_date,
                    new_date=new_date,
                    existing_urls=existing[3] or [],
                    new_url=source_url or "",
                    raw_data_path=raw_data_path,
                    data_job_id=data_job_id,
                )
                return existing[0]

        if existing:
            next_accuracy = self._best_date_accuracy(existing[2], date_accuracy)
            next_date = self._next_event_date(existing[1], existing[2], event.date, date_accuracy)
            cur.execute(
                """
                UPDATE release_events
                SET title = COALESCE(%s, title),
                    date = COALESCE(%s, date),
                    platform_ids = array(SELECT DISTINCT unnest(COALESCE(platform_ids, '{}'::text[]) || %s::text[])),
                    source_ids = array(SELECT DISTINCT unnest(COALESCE(source_ids, '{}'::text[]) || %s::text[])),
                    source_urls = array(SELECT DISTINCT unnest(COALESCE(source_urls, '{}'::text[]) || %s::text[])),
                    launch_time_utc = COALESCE(%s, launch_time_utc),
                    status = CASE
                        WHEN release_events.status IN ('released', 'cancelled') THEN release_events.status
                        ELSE %s
                    END,
                    confidence = GREATEST(confidence, %s),
                    date_accuracy = %s,
                    last_verified_at = now(),
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    event.title,
                    next_date,
                    platform_ids,
                    source_ids,
                    source_urls,
                    event.launch_time_utc,
                    status,
                    confidence,
                    next_accuracy,
                    existing[0],
                ),
            )
            return existing[0]

        cur.execute(
            """
            INSERT INTO release_events (
                game_id, event_type, title, date, launch_time_utc, date_accuracy,
                platform_ids, region, status, confidence, source_ids, source_urls,
                last_verified_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            RETURNING id
            """,
            (
                game_id,
                event.event_type,
                event.title or self._event_title(item, event),
                new_date,
                event.launch_time_utc,
                date_accuracy,
                platform_ids,
                region,
                status,
                confidence,
                source_ids,
                source_urls,
            ),
        )
        return cur.fetchone()[0]

    def _date_accuracy(self, value: str | None) -> str:
        return value if value in DATE_ACCURACY_RANK else "unknown"

    def _best_date_accuracy(self, existing: str | None, incoming: str | None) -> str:
        existing_value = self._date_accuracy(existing)
        incoming_value = self._date_accuracy(incoming)
        if DATE_ACCURACY_RANK[incoming_value] > DATE_ACCURACY_RANK[existing_value]:
            return incoming_value
        return existing_value

    def _next_event_date(
        self,
        existing_date,
        existing_accuracy: str | None,
        incoming_date: str | None,
        incoming_accuracy: str | None,
    ) -> str | None:
        if not incoming_date:
            return None
        if existing_date is None:
            return incoming_date
        existing_value = self._date_accuracy(existing_accuracy)
        incoming_value = self._date_accuracy(incoming_accuracy)
        if DATE_ACCURACY_RANK[incoming_value] > DATE_ACCURACY_RANK[existing_value]:
            return incoming_date
        return None

    def _clamp_confidence(self, value: int) -> int:
        return max(0, min(100, int(value)))

    def _event_title(self, item: CollectedGame, event: CollectedEvent) -> str:
        label = event.event_type.replace("_", " ")
        return f"{item.title} {label}"

    def _upsert_store_link(
        self,
        cur,
        game_id: str,
        game_platform_ids: list[str],
        link: StoreLink,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None = None,
    ) -> None:
        platform_ids = self._platform_ids(link.platform_slugs) if link.platform_slugs else game_platform_ids
        platform_id = platform_ids[0] if platform_ids else None
        link_id = link.id or f"{link.store_name}:{link.product_id or slugify(link.url)}"
        metadata = self._store_link_metadata(link, item, raw_data_path, data_job_id)
        cur.execute(
            """
            INSERT INTO store_links (
                id, game_id, platform_id, store_name, url, product_id, sku_id,
                np_title_id, edition_name, edition_type, edition_features,
                price_text, price, currency, preorder_available, wishlist_available,
                demo_available, release_date_text, metadata, last_checked_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                game_id = EXCLUDED.game_id,
                platform_id = COALESCE(EXCLUDED.platform_id, store_links.platform_id),
                url = EXCLUDED.url,
                product_id = COALESCE(EXCLUDED.product_id, store_links.product_id),
                sku_id = COALESCE(EXCLUDED.sku_id, store_links.sku_id),
                np_title_id = COALESCE(EXCLUDED.np_title_id, store_links.np_title_id),
                edition_name = COALESCE(EXCLUDED.edition_name, store_links.edition_name),
                edition_type = COALESCE(EXCLUDED.edition_type, store_links.edition_type),
                edition_features = COALESCE(NULLIF(EXCLUDED.edition_features, '{}'::text[]), store_links.edition_features),
                price_text = COALESCE(EXCLUDED.price_text, store_links.price_text),
                price = COALESCE(EXCLUDED.price, store_links.price),
                currency = COALESCE(EXCLUDED.currency, store_links.currency),
                preorder_available = COALESCE(EXCLUDED.preorder_available, store_links.preorder_available),
                wishlist_available = CASE
                    WHEN EXCLUDED.store_name = 'steam' THEN EXCLUDED.wishlist_available
                    ELSE COALESCE(EXCLUDED.wishlist_available, store_links.wishlist_available)
                END,
                demo_available = COALESCE(EXCLUDED.demo_available, store_links.demo_available),
                release_date_text = COALESCE(EXCLUDED.release_date_text, store_links.release_date_text),
                metadata = COALESCE(store_links.metadata, '{}'::jsonb) || COALESCE(EXCLUDED.metadata, '{}'::jsonb),
                last_checked_at = now(),
                updated_at = now()
            """,
            (
                link_id,
                game_id,
                platform_id,
                link.store_name,
                link.url,
                link.product_id,
                link.sku_id,
                link.np_title_id,
                link.edition_name,
                link.edition_type,
                link.edition_features,
                link.price_text,
                link.price,
                link.currency,
                link.preorder_available,
                link.wishlist_available,
                link.demo_available,
                link.release_date_text,
                _json(metadata),
            ),
        )
        self._record_store_link_observations(
            cur,
            game_id=game_id,
            store_link_id=link_id,
            platform_id=platform_id,
            link=link,
            item=item,
            raw_data_path=raw_data_path,
            data_job_id=data_job_id,
            metadata=metadata,
        )

    def _store_link_metadata(
        self,
        link: StoreLink,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None,
    ) -> dict[str, Any]:
        metadata = dict(link.metadata or {})
        evidence = {
            "sourceSlug": item.source_slug,
            "gameSourceUrl": item.source_url,
            "storeUrl": link.url,
            "rawDataPath": raw_data_path,
            "dataJobId": data_job_id,
        }
        metadata["evidence"] = {key: value for key, value in evidence.items() if value}
        return metadata

    def _record_store_link_observations(
        self,
        cur,
        game_id: str,
        store_link_id: str,
        platform_id: str | None,
        link: StoreLink,
        item: CollectedGame,
        raw_data_path: str | None,
        data_job_id: str | None,
        metadata: dict[str, Any],
    ) -> None:
        fact_key = f"{link.store_name}:{link.product_id or store_link_id}"
        observed_value = {
            "storeName": link.store_name,
            "url": link.url,
            "productId": link.product_id,
            "skuId": link.sku_id,
            "npTitleId": link.np_title_id,
            "editionName": link.edition_name,
            "editionType": link.edition_type,
            "editionFeatures": link.edition_features,
            "priceText": link.price_text,
            "releaseDateText": link.release_date_text,
            "metadata": link.metadata,
        }
        normalized_value = {
            "storeName": link.store_name,
            "url": link.url,
            "platformId": platform_id,
            "platformSlugs": link.platform_slugs,
            "productId": link.product_id,
            "skuId": link.sku_id,
            "npTitleId": link.np_title_id,
            "price": link.price,
            "currency": link.currency,
            "preorderAvailable": link.preorder_available,
            "wishlistAvailable": link.wishlist_available,
            "demoAvailable": link.demo_available,
        }
        self._insert_source_observation(
            cur,
            game_id=game_id,
            store_link_id=store_link_id,
            source_slug=item.source_slug,
            source_url=link.url,
            raw_data_path=raw_data_path,
            data_job_id=data_job_id,
            fact_type="store_link",
            fact_key=fact_key,
            observed_value=observed_value,
            normalized_value=normalized_value,
            confidence=85,
            metadata={"source": "collector_store_link", "evidence": metadata.get("evidence", {})},
        )
        if link.price is not None or link.price_text:
            self._insert_source_observation(
                cur,
                game_id=game_id,
                store_link_id=store_link_id,
                source_slug=item.source_slug,
                source_url=link.url,
                raw_data_path=raw_data_path,
                data_job_id=data_job_id,
                fact_type="price",
                fact_key=f"price:{fact_key}",
                observed_value={"priceText": link.price_text},
                normalized_value={"price": link.price, "currency": link.currency},
                confidence=80,
                metadata={"source": "collector_store_link"},
            )
        self._record_store_boolean_observation(
            cur, game_id, store_link_id, item, link, raw_data_path, data_job_id,
            "demo_availability", "demoAvailable", link.demo_available,
        )
        self._record_store_boolean_observation(
            cur, game_id, store_link_id, item, link, raw_data_path, data_job_id,
            "preorder_availability", "preorderAvailable", link.preorder_available,
        )
        self._record_store_boolean_observation(
            cur, game_id, store_link_id, item, link, raw_data_path, data_job_id,
            "wishlist_availability", "wishlistAvailable", link.wishlist_available,
        )
        if link.metadata:
            self._insert_source_observation(
                cur,
                game_id=game_id,
                store_link_id=store_link_id,
                source_slug=item.source_slug,
                source_url=link.url,
                raw_data_path=raw_data_path,
                data_job_id=data_job_id,
                fact_type="store_metadata",
                fact_key=f"store_metadata:{fact_key}",
                observed_value=link.metadata,
                normalized_value=metadata,
                confidence=75,
                metadata={"source": "collector_store_link"},
            )

    def _record_store_boolean_observation(
        self,
        cur,
        game_id: str,
        store_link_id: str,
        item: CollectedGame,
        link: StoreLink,
        raw_data_path: str | None,
        data_job_id: str | None,
        fact_type: str,
        field_name: str,
        value: bool | None,
    ) -> None:
        if value is None:
            return
        fact_key = f"{field_name}:{link.store_name}:{link.product_id or store_link_id}"
        self._insert_source_observation(
            cur,
            game_id=game_id,
            store_link_id=store_link_id,
            source_slug=item.source_slug,
            source_url=link.url,
            raw_data_path=raw_data_path,
            data_job_id=data_job_id,
            fact_type=fact_type,
            fact_key=fact_key,
            observed_value={field_name: value},
            normalized_value={field_name: value},
            confidence=85,
            metadata={"source": "collector_store_link"},
        )

    def create_date_conflict_alert(
        self,
        game_id: str,
        release_event_id: str,
        source_id: str | None,
        existing_date: str,
        new_date: str,
        existing_urls: list[str],
        new_url: str,
        raw_data_path: str | None,
        data_job_id: str | None = None,
    ) -> None:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (
                    alert_type, severity, title, description, game_id,
                    release_event_id, source_id, data_job_id, conflict_detail
                )
                VALUES (
                    'date_conflict', 'warning', 'Release date conflict',
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    f"Existing release date {existing_date} conflicts with official source date {new_date}.",
                    game_id,
                    release_event_id,
                    source_id,
                    data_job_id,
                    _json({
                        "existing": {"date": existing_date, "urls": existing_urls},
                        "incoming": {"date": new_date, "url": new_url, "rawDataPath": raw_data_path},
                        "policy": "do_not_silently_overwrite_official_fact",
                    }),
                ),
            )

    def record_source_success(self, source_slug: str) -> None:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sources
                SET consecutive_failures = 0,
                    last_fetched_at = now(),
                    last_successful_fetch_at = now(),
                    updated_at = now()
                WHERE slug = %s
                """,
                (source_slug,),
            )

    def record_source_failure(
        self,
        source_slug: str,
        data_job_id: str | None,
        error_message: str,
        threshold: int = 3,
    ) -> None:
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE sources
                SET consecutive_failures = consecutive_failures + 1,
                    last_fetched_at = now(),
                    updated_at = now()
                WHERE slug = %s
                RETURNING id, name, consecutive_failures
                """,
                (source_slug,),
            )
            row = cur.fetchone()
            if not row:
                return
            source_id, source_name, failure_count = row
            if failure_count < threshold:
                return
            cur.execute(
                """
                INSERT INTO alerts (
                    alert_type, severity, title, description,
                    source_id, data_job_id, conflict_detail
                )
                SELECT
                    'source_failure',
                    'critical',
                    'Source consecutive failures',
                    %s,
                    %s,
                    %s,
                    %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM alerts
                    WHERE alert_type = 'source_failure'
                      AND source_id = %s
                      AND status = 'open'
                )
                """,
                (
                    f"{source_name} failed {failure_count} consecutive times.",
                    source_id,
                    data_job_id,
                    _json({
                        "sourceSlug": source_slug,
                        "failureCount": failure_count,
                        "threshold": threshold,
                        "lastError": error_message,
                    }),
                    source_id,
                ),
            )
