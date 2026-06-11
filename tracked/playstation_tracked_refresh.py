from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.playstation import _fetch_concept_detail, apply_concept_page_store_links, parse_concept_detail_payload
from core.config import load_settings
from core.http_client import fetch_text
from core.job_logger import finish_job, resolve_job_status, start_job
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
        raise RuntimeError("DATABASE_URL is required for PlayStation tracked refresh")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": ["playstation"],
                "mode": "playstation_tracked_refresh",
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = _tracked_playstation_rows(store, limit)
        print(f"中文日志：PlayStation 追踪刷新找到 {len(rows)} 个待刷新游戏，limit={limit}", flush=True)
        for row in rows:
            concept_id = row["concept_id"]
            try:
                detail_payload = _fetch_concept_detail(concept_id, settings.user_agent)
                game = parse_concept_detail_payload(detail_payload, fallback=_fallback_concept(row))
                job.processed_count += 1
                if game is None:
                    job.skipped_count += 1
                else:
                    page_html = fetch_text(game.source_url, settings.user_agent)
                    game = apply_concept_page_store_links(game, page_html, detail_payload=detail_payload)
                    store.upsert_collected_game(game, raw_data_path=None, data_job_id=job.job_id)
                    job.updated_count += 1
                store.conn.commit()
            except Exception as exc:
                job.error_count += 1
                job.errors.append({"conceptId": concept_id, "message": str(exc)})
                try:
                    store.conn.rollback()
                except Exception:
                    store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        record_refresh_source_status(store, "playstation", job.job_id, status, error_message)
        print(
            f"中文日志：PlayStation 追踪刷新完成，状态={status}，处理={job.processed_count}，失败={job.error_count}",
            flush=True,
        )
        return RefreshResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _tracked_playstation_rows(store: NeonStore, limit: int) -> list[dict[str, Any]]:
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
              SELECT
                g.id,
                g.title,
                g.official_url,
                substring(g.official_url from '/concept/([0-9]+)') AS concept_id,
                COALESCE(fr.release_date, g.primary_release_date) AS release_date,
                COALESCE(fr.date_accuracy, 'unknown') AS date_accuracy,
                ps.platform_slugs,
                COALESCE(sl.last_checked_at, g.last_scraped_at, g.updated_at, g.created_at) AS last_refresh_at
              FROM games g
              LEFT JOIN first_release fr ON fr.game_id = g.id
              LEFT JOIN LATERAL (
                SELECT array_agg(DISTINCT p.slug) AS platform_slugs
                FROM game_platforms gp
                JOIN platforms p ON p.id = gp.platform_id
                WHERE gp.game_id = g.id
                  AND p.slug = ANY(%s)
              ) ps ON true
              LEFT JOIN LATERAL (
                SELECT last_checked_at
                FROM store_links
                WHERE game_id = g.id
                  AND store_name = 'playstation_store'
                ORDER BY last_checked_at DESC NULLS LAST
                LIMIT 1
              ) sl ON true
              WHERE g.status = 'upcoming'
                AND g.official_url ~ '/concept/[0-9]+'
                AND array_length(ps.platform_slugs, 1) IS NOT NULL
                AND (
                  COALESCE(fr.release_date, g.primary_release_date) IS NULL
                  OR COALESCE(fr.release_date, g.primary_release_date)::date >= CURRENT_DATE
                )
            )
            SELECT *
            FROM tracked
            WHERE concept_id IS NOT NULL
            ORDER BY last_refresh_at ASC NULLS FIRST,
              release_date ASC NULLS LAST,
              title ASC
            LIMIT %s
            """,
            (["ps5", "ps4"], limit),
        )
        columns = [column.name for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _fallback_concept(row: dict[str, Any]) -> dict[str, Any]:
    concept_id = str(row["concept_id"])
    return {
        "id": concept_id,
        "name": row["title"],
        "defaultProduct": {
            "platforms": _platform_names(row.get("platform_slugs")),
            "type": "GAME",
            "subType": "FULL_GAME",
        },
    }


def _platform_names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    names: list[str] = []
    for item in values:
        if item == "ps5":
            names.append("PS5")
        elif item == "ps4":
            names.append("PS4")
    return names or ["PS5"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh tracked upcoming PlayStation games and record game_updates changes.")
    parser.add_argument("--limit", type=int, default=80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_refresh(limit=args.limit)
    print(
        "playstation-tracked-refresh completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
