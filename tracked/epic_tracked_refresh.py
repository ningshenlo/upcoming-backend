from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.epic import fetch_catalog_tag_names, fetch_offer_payload, parse_offer_payload
from core.config import load_settings
from core.job_logger import finish_job, resolve_job_status, start_job
from core.models import CollectedGame
from core.neon import NeonStore
from tracked.common import record_refresh_source_status


@dataclass(frozen=True)
class RefreshResult:
    status: str
    processed_count: int
    failed_count: int


def run_refresh(limit: int) -> RefreshResult:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for Epic tracked refresh")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": ["epic"],
                "mode": "epic_tracked_refresh",
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = _tracked_epic_rows(store, limit)
        print(f"中文日志：Epic 追踪刷新找到 {len(rows)} 个待刷新游戏，limit={limit}", flush=True)
        tag_names_by_id = fetch_catalog_tag_names(settings.user_agent) if rows else {}
        for row in rows:
            namespace, offer_id = _offer_identity(row)
            try:
                if not namespace or not offer_id:
                    raise RuntimeError("Epic row is missing namespace or offer id")
                payload = fetch_offer_payload(namespace, offer_id, settings.user_agent)
                game = _matching_game(parse_offer_payload(payload, tag_names_by_id=tag_names_by_id), row)
                job.processed_count += 1
                if game is None:
                    job.skipped_count += 1
                else:
                    store.upsert_collected_game(game, raw_data_path=None, data_job_id=job.job_id)
                    job.updated_count += 1
                store.conn.commit()
            except Exception as exc:
                job.error_count += 1
                job.errors.append({"namespace": namespace, "offerId": offer_id, "message": str(exc)})
                try:
                    store.conn.rollback()
                except Exception:
                    store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        record_refresh_source_status(store, "epic", job.job_id, status, error_message)
        print(
            f"中文日志：Epic 追踪刷新完成，状态={status}，处理={job.processed_count}，失败={job.error_count}",
            flush=True,
        )
        return RefreshResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _tracked_epic_rows(store: NeonStore, limit: int) -> list[dict[str, Any]]:
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
                sl.product_id,
                sl.sku_id,
                sl.metadata,
                COALESCE(fr.release_date, g.primary_release_date) AS release_date,
                COALESCE(fr.date_accuracy, 'unknown') AS date_accuracy,
                ps.platform_slugs,
                COALESCE(sl.last_checked_at, g.last_scraped_at, g.updated_at, g.created_at) AS last_refresh_at
              FROM games g
              JOIN store_links sl ON sl.game_id = g.id
                AND sl.store_name = 'epic_games_store'
              LEFT JOIN first_release fr ON fr.game_id = g.id
              LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT p.slug) AS platform_slugs
                FROM game_platforms gp
                JOIN platforms p ON p.id = gp.platform_id
                WHERE gp.game_id = g.id
                  AND p.slug = ANY(%s)
              ) ps ON true
              WHERE g.status = 'upcoming'
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
            (["pc", "epic-games-store"], limit),
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _offer_identity(row: dict[str, Any]) -> tuple[str | None, str | None]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    namespace = _string_value(
        metadata.get("namespace")
        or metadata.get("epicNamespace")
        or metadata.get("sandboxId")
        or metadata.get("epicSandboxId")
    )
    offer_id = _string_value(row.get("sku_id") or metadata.get("offerId") or metadata.get("epicOfferId"))
    product_id = _string_value(row.get("product_id"))
    if product_id and ":" in product_id:
        fallback_namespace, fallback_offer_id = product_id.split(":", 1)
        namespace = namespace or fallback_namespace
        offer_id = offer_id or fallback_offer_id
    return namespace, offer_id


def _matching_game(games: list[CollectedGame], row: dict[str, Any]) -> CollectedGame | None:
    product_id = str(row.get("product_id") or "")
    offer_id = str(row.get("sku_id") or "")
    namespace, metadata_offer_id = _offer_identity(row)
    offer_id = offer_id or str(metadata_offer_id or "")
    store_url = str(row.get("store_url") or "").rstrip("/")
    for game in games:
        if product_id and str(game.external_ids.get("productId") or "") == product_id:
            return game
        if product_id and str(game.external_ids.get("epicProductId") or "") == product_id:
            return game
        if offer_id and str(game.external_ids.get("epicOfferId") or "") == offer_id:
            return game
        game_namespace = str(game.external_ids.get("epicNamespace") or "")
        game_offer_id = str(game.external_ids.get("epicOfferId") or "")
        if namespace and game_namespace == namespace and (not offer_id or game_offer_id == offer_id):
            return game
        if store_url and game.source_url.rstrip("/") == store_url:
            return game
        for link in game.store_links:
            if product_id and str(link.product_id or "") == product_id:
                return game
            if offer_id and str(link.sku_id or "") == offer_id:
                return game
            if store_url and link.url.rstrip("/") == store_url:
                return game
    return games[0] if games else None


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh tracked upcoming Epic Games Store games and record game_updates changes.")
    parser.add_argument("--limit", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_refresh(limit=args.limit)
    print(
        "epic-tracked-refresh completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
