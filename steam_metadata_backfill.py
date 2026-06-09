from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from collectors.steam import STEAM_APPDETAILS_URL, STEAM_APP_PAGE_URL, apply_app_page_tags, apply_appdetails, parse_app_page_tags
from core.config import load_settings
from core.http_client import fetch_json, fetch_text
from core.job_logger import finish_job, resolve_job_status, start_job
from core.models import CollectedGame
from core.neon import NeonStore


@dataclass(frozen=True)
class BackfillResult:
    status: str
    processed_count: int
    failed_count: int


def run_backfill(limit: int) -> BackfillResult:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for Steam metadata backfill")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": ["steam"],
                "mode": "steam_metadata_backfill",
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = _missing_steam_metadata_rows(store, limit)
        for row in rows:
            app_id = row["steam_app_id"]
            try:
                details_url = STEAM_APPDETAILS_URL.format(app_id=app_id)
                payload = fetch_json(details_url, settings.user_agent)
                game = _row_to_game(row)
                enriched = apply_appdetails(game, payload)
                job.processed_count += 1
                if enriched is None:
                    job.skipped_count += 1
                else:
                    page_html = fetch_text(STEAM_APP_PAGE_URL.format(app_id=app_id), settings.user_agent)
                    enriched = apply_app_page_tags(enriched, parse_app_page_tags(page_html))
                    store.upsert_collected_game(enriched, raw_data_path=None, data_job_id=job.job_id)
                    job.updated_count += 1
                store.conn.commit()
            except Exception as exc:
                job.error_count += 1
                job.errors.append({"appId": app_id, "message": str(exc)})
                try:
                    store.conn.rollback()
                except Exception:
                    store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        return BackfillResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _missing_steam_metadata_rows(store: NeonStore, limit: int) -> list[dict[str, Any]]:
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            WITH first_release AS (
              SELECT DISTINCT ON (game_id)
                game_id,
                date AS release_date,
                date_accuracy
              FROM release_events
              WHERE event_type = 'release'
              ORDER BY game_id, date NULLS LAST, created_at
            )
            SELECT
              g.id,
              g.title,
              g.official_url,
              g.cover_image_url,
              g.steam_app_id,
              COALESCE(fr.release_date, g.primary_release_date) AS release_date,
              COALESCE(fr.date_accuracy, 'unknown') AS date_accuracy
            FROM games g
            JOIN game_platforms gp ON gp.game_id = g.id
            JOIN platforms p ON p.id = gp.platform_id AND p.slug = 'steam'
            LEFT JOIN first_release fr ON fr.game_id = g.id
            LEFT JOIN store_links sl ON sl.game_id = g.id AND sl.store_name = 'steam'
            WHERE g.status = 'upcoming'
              AND g.steam_app_id IS NOT NULL
              AND COALESCE(fr.release_date, g.primary_release_date)::date >= CURRENT_DATE
              AND (
                sl.id IS NULL
                OR NULLIF(sl.price_text, '') IS NULL
                OR NOT (
                  (jsonb_typeof(sl.metadata->'tags') = 'array' AND jsonb_array_length(sl.metadata->'tags') > 0)
                  OR (jsonb_typeof(sl.metadata->'categories') = 'array' AND jsonb_array_length(sl.metadata->'categories') > 0)
                  OR (jsonb_typeof(sl.metadata->'genres') = 'array' AND jsonb_array_length(sl.metadata->'genres') > 0)
                )
              )
            ORDER BY COALESCE(fr.release_date, g.primary_release_date) NULLS LAST, g.updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _row_to_game(row: dict[str, Any]) -> CollectedGame:
    app_id = int(row["steam_app_id"])
    source_url = row["official_url"] or f"https://store.steampowered.com/app/{app_id}/"
    release_date = row["release_date"]
    return CollectedGame(
        title=row["title"],
        source_slug="steam",
        source_url=source_url,
        platform_slugs=["pc", "steam"],
        release_date=release_date.date().isoformat() if hasattr(release_date, "date") else str(release_date),
        date_accuracy=row["date_accuracy"] or "unknown",
        cover_image_url=row["cover_image_url"],
        external_ids={"steamAppId": app_id},
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Steam store metadata for existing upcoming games.")
    parser.add_argument("--limit", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_backfill(limit=args.limit)
    print(
        "steam-metadata-backfill completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
