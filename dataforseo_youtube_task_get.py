from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from core.config import Settings, load_settings
from core.job_logger import finish_job, resolve_job_status, start_job
from core.neon import NeonStore
from hot_trackers.youtube import (
    GameRow,
    candidate_from_dataforseo_item,
    score_candidate,
    select_best_candidate,
    _candidate_metadata,
    _upsert_tracked_video,
)

DFS_YOUTUBE_TASK_SOURCE = "serp/youtube/organic/task_post"
DFS_YOUTUBE_TASK_GET_URL = "https://api.dataforseo.com/v3/serp/youtube/organic/task_get/advanced/{task_id}"
DFS_OK_STATUS_CODE = 20000
DFS_TERMINAL_TASK_STATUS_CODES = {40000, 40401, 40403, 50000}

DFSTASK_SUBMITTED = "submitted"
DFSTASK_PENDING = "pending"
DFSTASK_PROCESSING = "processing"
DFSTASK_DONE = "done"
DFSTASK_FAILED = "failed"


@dataclass(frozen=True)
class DfsTaskRow:
    task_id: str
    source: str


@dataclass(frozen=True)
class DfsYoutubeTaskGetResult:
    status: str
    processed_count: int
    failed_count: int


class DfsTaskNotReadyError(RuntimeError):
    pass


def run(settings: Settings, limit: int) -> DfsYoutubeTaskGetResult:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for DataForSEO YouTube task_get")
    if not settings.dataforseo_api_key:
        raise RuntimeError("DFS or DATAFORSEO_API_KEY is required for DataForSEO task_get")

    with NeonStore(settings.database_url) as store:
        job = start_job(
            store,
            "youtube_track",
            {
                "mode": "dataforseo_youtube_task_get",
                "source": DFS_YOUTUBE_TASK_SOURCE,
                "limit": limit,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        print(
            "dataforseo youtube task_get: started "
            f"job_id={job.job_id} limit={limit}",
            flush=True,
        )

        tasks = _claim_pending_tasks(store, limit)
        store.conn.commit()
        print(f"dataforseo youtube task_get: claimed tasks={len(tasks)}", flush=True)
        if not tasks:
            print("dataforseo youtube task_get: no pending tasks; finishing without API calls", flush=True)

        for task in tasks:
            job.processed_count += 1
            try:
                print(
                    "dataforseo youtube task_get: processing "
                    f"task_id={task.task_id} source={task.source}",
                    flush=True,
                )
                game = _game_row_for_task(store, task)
                if game is None:
                    print(
                        "dataforseo youtube task_get: no game mapping "
                        f"task_id={task.task_id} source={task.source}",
                        file=sys.stderr,
                        flush=True,
                    )
                    job.skipped_count += 1
                    _mark_task(store, task.task_id, DFSTASK_PENDING, "No game mapping found for task.")
                    store.conn.commit()
                    continue

                payload = _fetch_dfs_result(task.task_id, settings)
                query = _extract_keyword(payload) or ""
                candidates = parse_dataforseo_youtube_candidates(payload)
                best = select_best_candidate(game, candidates)
                print(
                    "dataforseo youtube task_get: fetched result "
                    f"task_id={task.task_id} game_id={game.id} title={game.title!r} "
                    f"query={query!r} candidates={len(candidates)}",
                    flush=True,
                )
                metadata = {
                    "source": "dataforseo",
                    "dataforseoTaskId": task.task_id,
                    "dataforseoSource": task.source,
                    "candidateCount": len(candidates),
                    "candidates": [_candidate_metadata(score_candidate(game, candidate)) for candidate in candidates[:5]],
                }

                if best is None:
                    print(
                        "dataforseo youtube task_get: no candidate selected "
                        f"task_id={task.task_id} game_id={game.id}",
                        flush=True,
                    )
                    _upsert_tracked_video(store, game, None, query, "not_found", metadata)
                else:
                    print(
                        "dataforseo youtube task_get: selected candidate "
                        f"task_id={task.task_id} game_id={game.id} video_id={best.video_id} "
                        f"confidence={best.match_confidence} reasons={','.join(best.match_reasons)}",
                        flush=True,
                    )
                    _upsert_tracked_video(store, game, best, query, "found", metadata)

                _mark_task(store, task.task_id, DFSTASK_DONE)
                job.updated_count += 1
                store.conn.commit()
            except DfsTaskNotReadyError as exc:
                print(
                    f"dataforseo youtube task_get: task not ready task_id={task.task_id} message={exc}",
                    flush=True,
                )
                job.skipped_count += 1
                _mark_task(store, task.task_id, DFSTASK_PENDING, str(exc))
                store.conn.commit()
            except Exception as exc:
                print(
                    f"dataforseo youtube task_get: task failed task_id={task.task_id} error={exc}",
                    file=sys.stderr,
                    flush=True,
                )
                job.error_count += 1
                job.errors.append({"taskId": task.task_id, "message": str(exc)})
                try:
                    store.conn.rollback()
                    _mark_task(store, task.task_id, DFSTASK_FAILED, str(exc))
                    store.conn.commit()
                except Exception:
                    try:
                        store.conn.rollback()
                    except Exception:
                        store.reconnect()

        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors[:3]) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        print(
            "dataforseo youtube task_get: finished "
            f"job_id={job.job_id} status={status} processed={job.processed_count} "
            f"updated={job.updated_count} skipped={job.skipped_count} errors={job.error_count}",
            flush=True,
        )
        return DfsYoutubeTaskGetResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def parse_dataforseo_youtube_candidates(payload: dict[str, Any]) -> list:
    candidates = []
    for result in _iter_dataforseo_results(payload):
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = candidate_from_dataforseo_item(item)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _claim_pending_tasks(store: NeonStore, limit: int) -> list[DfsTaskRow]:
    if limit <= 0:
        return []
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            WITH claim AS (
              SELECT task_id
              FROM dfstasks
              WHERE status IN (%s, %s)
                AND (source = %s OR source LIKE %s)
              ORDER BY updated_at ASC
              LIMIT %s
              FOR UPDATE SKIP LOCKED
            )
            UPDATE dfstasks d
            SET status = %s,
                updated_at = now()
            FROM claim
            WHERE d.task_id = claim.task_id
            RETURNING d.task_id, d.source
            """,
            (
                DFSTASK_PENDING,
                DFSTASK_SUBMITTED,
                DFS_YOUTUBE_TASK_SOURCE,
                f"{DFS_YOUTUBE_TASK_SOURCE}:%",
                limit,
                DFSTASK_PROCESSING,
            ),
        )
        return [DfsTaskRow(task_id=row[0], source=row[1]) for row in cur.fetchall()]


def _game_row_for_task(store: NeonStore, task: DfsTaskRow) -> GameRow | None:
    game_id = _game_id_from_source(task.source)
    assert store.conn is not None
    with store.conn.cursor() as cur:
        if game_id:
            cur.execute(_GAME_BY_ID_SQL, (game_id,))
        else:
            cur.execute(
                _GAME_BY_TASK_ID_SQL,
                (
                    task.task_id,
                    task.task_id,
                    task.task_id,
                ),
            )
        row = cur.fetchone()
    if not row:
        return None
    return GameRow(
        id=row[0],
        title=row[1],
        aliases=list(row[2] or []),
        trailer_url=row[3],
        publishers=list(row[4] or []),
        developers=list(row[5] or []),
    )


def _fetch_dfs_result(task_id: str, settings: Settings) -> dict[str, Any]:
    headers = {
        "Authorization": _authorization_header(settings.dataforseo_api_key or ""),
        "Content-Type": "application/json",
    }
    url = DFS_YOUTUBE_TASK_GET_URL.format(task_id=task_id)
    with httpx.Client(timeout=30.0, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()

    if payload.get("status_code") != DFS_OK_STATUS_CODE:
        raise RuntimeError(f"DFS API error: {payload.get('status_message')}")

    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    if not tasks:
        raise RuntimeError("DFS returned an empty tasks list")

    task = tasks[0]
    task_status = task.get("status_code")
    task_message = task.get("status_message")
    if task_status == DFS_OK_STATUS_CODE:
        return payload
    if task_status in DFS_TERMINAL_TASK_STATUS_CODES:
        raise RuntimeError(f"DFS task failed: {task_status} {task_message}")
    raise DfsTaskNotReadyError(f"DFS task not ready: {task_status} {task_message}")


def _mark_task(store: NeonStore, task_id: str, status: str, error_msg: str | None = None) -> None:
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute(
            """
            UPDATE dfstasks
            SET status = %s,
                error_msg = %s,
                updated_at = now()
            WHERE task_id = %s
            """,
            (status, error_msg[:500] if error_msg else None, task_id),
        )


def _iter_dataforseo_results(payload: dict[str, Any]):
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        for task in tasks:
            if isinstance(task, dict) and isinstance(task.get("result"), list):
                yield from task["result"]
        return

    results = payload.get("result")
    if isinstance(results, list):
        yield from results


def _extract_keyword(payload: dict[str, Any]) -> str | None:
    for result in _iter_dataforseo_results(payload):
        if isinstance(result, dict) and isinstance(result.get("keyword"), str):
            return result["keyword"]
    tasks = payload.get("tasks")
    if isinstance(tasks, list) and tasks:
        data = tasks[0].get("data") if isinstance(tasks[0], dict) else None
        if isinstance(data, dict) and isinstance(data.get("keyword"), str):
            return data["keyword"]
    return None


def _authorization_header(api_key: str) -> str:
    api_key = api_key.strip()
    if api_key.lower().startswith("basic "):
        return api_key
    return f"Basic {api_key}"


def _game_id_from_source(source: str) -> str | None:
    marker = f"{DFS_YOUTUBE_TASK_SOURCE}:"
    if not source.startswith(marker):
        return None
    value = source[len(marker) :].strip()
    if value.startswith("game_id="):
        value = value[len("game_id=") :]
    return value or None


_GAME_BY_ID_SQL = """
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
WHERE g.id = %s
LIMIT 1
"""

_GAME_BY_TASK_ID_SQL = """
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
FROM youtube_tracked_videos ytv
JOIN games g ON g.id = ytv.game_id
LEFT JOIN company_names cn ON cn.game_id = g.id
WHERE ytv.metadata->>'dataforseoTaskId' = %s
   OR ytv.metadata->>'dfsTaskId' = %s
   OR ytv.metadata->>'taskId' = %s
LIMIT 1
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch completed DataForSEO YouTube Organic task results.")
    parser.add_argument("--limit", type=int, default=_int_env("DATAFORSEO_YOUTUBE_TASK_GET_LIMIT", 50))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(load_settings(), limit=args.limit)
    print(
        "dataforseo-youtube-task-get completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


def _int_env(name: str, default: int) -> int:
    import os

    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
