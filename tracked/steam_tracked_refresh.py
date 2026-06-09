from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from collectors.steam import STEAM_APPDETAILS_URL, STEAM_APP_PAGE_URL, apply_app_page_tags, apply_appdetails, parse_app_page_tags
from core.config import load_settings
from core.http_client import fetch_json, fetch_text
from core.job_logger import finish_job, resolve_job_status, start_job
from core.neon import NeonStore
from steam_metadata_backfill import _row_to_game


@dataclass(frozen=True)
class RefreshResult:
    status: str
    processed_count: int
    failed_count: int


def run_refresh(limit: int) -> RefreshResult:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for Steam tracked refresh")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": ["steam"],
                "mode": "steam_tracked_refresh",
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = _tracked_steam_rows(store, limit)
        for row in rows:
            app_id = row["steam_app_id"]
            try:
                game = _row_to_game(row)
                details_url = STEAM_APPDETAILS_URL.format(app_id=app_id)
                enriched = apply_appdetails(game, fetch_json(details_url, settings.user_agent))
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
        return RefreshResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _tracked_steam_rows(store: NeonStore, limit: int) -> list[dict[str, Any]]:
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
              ORDER BY game_id,
                CASE date_accuracy
                  WHEN 'exact' THEN 5
                  WHEN 'week' THEN 4
                  WHEN 'month' THEN 3
                  WHEN 'quarter' THEN 2
                  WHEN 'year' THEN 1
                  ELSE 0
                END DESC,
                date NULLS LAST,
                created_at ASC
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
            LEFT JOIN LATERAL (
              SELECT last_checked_at
              FROM store_links
              WHERE game_id = g.id
                AND store_name = 'steam'
              ORDER BY last_checked_at DESC NULLS LAST
              LIMIT 1
            ) sl ON true
            WHERE g.status = 'upcoming'
              AND g.steam_app_id IS NOT NULL
              AND (
                COALESCE(fr.release_date, g.primary_release_date) IS NULL
                OR COALESCE(fr.release_date, g.primary_release_date)::date >= CURRENT_DATE
              )
            ORDER BY
              COALESCE(sl.last_checked_at, g.last_scraped_at, g.updated_at, g.created_at) ASC NULLS FIRST,
              COALESCE(fr.release_date, g.primary_release_date) ASC NULLS LAST,
              g.updated_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh tracked upcoming Steam games and record game_updates changes.")
    parser.add_argument("--limit", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_refresh(limit=args.limit)
    print(
        "steam-tracked-refresh completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
