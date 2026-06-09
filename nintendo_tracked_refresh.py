from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from collectors.nintendo import parse_store_page
from core.config import load_settings
from core.http_client import fetch_text
from core.job_logger import finish_job, resolve_job_status, start_job
from core.models import CollectedGame
from core.neon import NeonStore


@dataclass(frozen=True)
class RefreshResult:
    status: str
    processed_count: int
    failed_count: int


def run_refresh(limit: int) -> RefreshResult:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for Nintendo tracked refresh")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": ["nintendo"],
                "mode": "nintendo_tracked_refresh",
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = _tracked_nintendo_rows(store, limit)
        for row in rows:
            store_url = row["store_url"]
            try:
                page_html = fetch_text(store_url, settings.user_agent)
                result = parse_store_page(page_html, store_url, limit=10)
                game = _matching_game(result.games, row)
                job.processed_count += 1
                if game is None:
                    job.skipped_count += 1
                else:
                    store.upsert_collected_game(game, raw_data_path=None, data_job_id=job.job_id)
                    job.updated_count += 1
                store.conn.commit()
            except Exception as exc:
                job.error_count += 1
                job.errors.append({"url": store_url, "message": str(exc)})
                try:
                    store.conn.rollback()
                except Exception:
                    store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        return RefreshResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _tracked_nintendo_rows(store: NeonStore, limit: int) -> list[dict[str, Any]]:
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
            ),
            tracked AS (
              SELECT DISTINCT ON (g.id)
                g.id,
                g.title,
                COALESCE(sl.url, g.official_url) AS store_url,
                sl.product_id AS nsuid,
                sl.sku_id,
                COALESCE(fr.release_date, g.primary_release_date) AS release_date,
                COALESCE(fr.date_accuracy, 'unknown') AS date_accuracy,
                ps.platform_slugs,
                COALESCE(sl.last_checked_at, g.last_scraped_at, g.updated_at, g.created_at) AS last_refresh_at
              FROM games g
              JOIN store_links sl ON sl.game_id = g.id
                AND sl.store_name = 'nintendo_eshop'
              LEFT JOIN first_release fr ON fr.game_id = g.id
              LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT p.slug) AS platform_slugs
                FROM game_platforms gp
                JOIN platforms p ON p.id = gp.platform_id
                WHERE gp.game_id = g.id
                  AND p.slug = ANY(%s)
              ) ps ON true
              WHERE g.status = 'upcoming'
                AND COALESCE(sl.url, g.official_url) IS NOT NULL
                AND array_length(ps.platform_slugs, 1) IS NOT NULL
                AND (
                  COALESCE(fr.release_date, g.primary_release_date) IS NULL
                  OR COALESCE(fr.release_date, g.primary_release_date)::date >= CURRENT_DATE
                )
              ORDER BY g.id,
                sl.last_checked_at ASC NULLS FIRST,
                sl.updated_at ASC NULLS FIRST
            )
            SELECT *
            FROM tracked
            ORDER BY last_refresh_at ASC NULLS FIRST,
              release_date ASC NULLS LAST,
              title ASC
            LIMIT %s
            """,
            (["nintendo-switch", "nintendo-switch-2"], limit),
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _matching_game(games: list[CollectedGame], row: dict[str, Any]) -> CollectedGame | None:
    nsuid = str(row.get("nsuid") or "")
    sku = str(row.get("sku_id") or "")
    store_url = str(row.get("store_url") or "").rstrip("/")
    for game in games:
        if nsuid and str(game.external_ids.get("nintendoNsuid") or "") == nsuid:
            return game
        if sku and str(game.external_ids.get("nintendoSku") or "") == sku:
            return game
        if store_url and game.source_url.rstrip("/") == store_url:
            return game
    return games[0] if games else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh tracked upcoming Nintendo eShop games and record game_updates changes.")
    parser.add_argument("--limit", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_refresh(limit=args.limit)
    print(
        "nintendo-tracked-refresh completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
