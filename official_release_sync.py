from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from core.config import load_settings
from core.job_logger import finish_job, resolve_job_status, start_job
from core.neon import NeonStore
from core.r2 import RawArchive


DEFAULT_COLLECTORS = ["steam", "nintendo", "playstation"]
SOURCE_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class SyncRunResult:
    status: str
    processed_count: int
    failed_count: int


def check_config() -> int:
    settings = load_settings()
    checks = {
        "DATABASE_URL": bool(settings.database_url),
        "R2_BUCKET_NAME": bool(settings.r2_bucket_name),
        "R2_ENDPOINT_URL": bool(settings.r2_endpoint_url),
        "R2_ACCESS_KEY_ID": bool(settings.r2_access_key_id),
        "R2_SECRET_ACCESS_KEY": bool(settings.r2_secret_access_key),
        "R2_WRANGLER_UPLOAD": settings.r2_use_wrangler,
        "REQUIRE_R2_ARCHIVE": settings.require_r2_archive,
    }
    for name, ok in checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    if settings.require_r2_archive and not settings.has_r2:
        print("R2 archive: incomplete")
        return 1
    print(f"R2 archive: {'enabled' if settings.has_r2 else 'local fallback'}")
    return 0


def run_sync(collector_names: Iterable[str], limit: int, dry_run: bool) -> SyncRunResult:
    settings = load_settings()
    archive = RawArchive(settings)
    collectors = list(collector_names)
    store_ctx = NeonStore(settings.database_url) if settings.database_url and not dry_run else None

    if store_ctx is None:
        return _run_without_db(collectors, limit, settings.user_agent, archive)

    with store_ctx as store:
        job = start_job(
            store,
            "official_release_sync",
            {
                "collectors": collectors,
                "limit": limit,
                "dryRun": dry_run,
                "startedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        try:
            timed_out_count = store.fail_stale_running_jobs()
            store.conn.commit()
            if timed_out_count:
                job.collector_results.append(
                    {"collector": "lifecycle", "status": "success", "timedOutJobs": timed_out_count}
                )
            archived_count = store.archive_expired_upcoming_games()
            store.conn.commit()
            if archived_count:
                job.skipped_count += archived_count
                job.collector_results.append(
                    {"collector": "lifecycle", "status": "success", "releasedCount": archived_count}
                )
            _run_collectors(collectors, limit, settings.user_agent, archive, store, job)
        except Exception as exc:
            job.error_count += 1
            job.errors.append({"collector": "entrypoint", "message": str(exc)})
            finish_job(store, job, "failed", error_message=str(exc))
            raise
        status = resolve_job_status(job)
        error_message = "; ".join(item["message"] for item in job.errors) if job.errors else None
        finish_job(store, job, status, error_message=error_message)
        return SyncRunResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def _run_without_db(collector_names, limit, user_agent, archive) -> SyncRunResult:
    total = 0
    failed = 0
    for name in collector_names:
        try:
            module = importlib.import_module(f"collectors.{name}")
            result = module.collect(limit=limit, user_agent=user_agent)
            raw_path = archive.write_json(result.source_slug, result.raw_payload)
            _validate_collector_result(name, limit, result)
            total += len(result.games)
            print(f"{name}: {len(result.games)} games, raw={raw_path}")
            for game in result.games[:5]:
                print(f"  - {game.title} | {game.release_date or 'TBA'} | {','.join(game.platform_slugs)}")
        except Exception as exc:
            failed += 1
            print(f"{name}: failed: {exc}", file=sys.stderr)
    if failed and total:
        return SyncRunResult(status="partial_success", processed_count=total, failed_count=failed)
    if failed:
        return SyncRunResult(status="failed", processed_count=total, failed_count=failed)
    return SyncRunResult(status="success", processed_count=total, failed_count=0)


def _run_collectors(collector_names, limit, user_agent, archive, store, job) -> int:
    total = 0
    for name in collector_names:
        raw_path = None
        try:
            module = importlib.import_module(f"collectors.{name}")
            result = module.collect(limit=limit, user_agent=user_agent)
            raw_path = archive.write_json(result.source_slug, result.raw_payload)
            store.reconnect()
            job.raw_data_path = raw_path
            if job.raw_data_paths is None:
                job.raw_data_paths = []
            job.raw_data_paths.append(raw_path)
            _validate_collector_result(name, limit, result)
            job.processed_count += len(result.games)
            for game in result.games:
                store.upsert_collected_game(game, raw_data_path=raw_path, data_job_id=job.job_id)
                job.updated_count += 1
            total += len(result.games)
            store.record_source_success(result.source_slug)
            store.conn.commit()
            job.collector_results.append(
                {"collector": name, "status": "success", "processedCount": len(result.games), "rawDataPath": raw_path}
            )
        except Exception as exc:
            message = str(exc)
            try:
                store.reconnect()
            except Exception:
                pass
            job.error_count += 1
            job.errors.append({"collector": name, "message": message})
            failed_result = {"collector": name, "status": "failed", "error": message}
            if raw_path:
                failed_result["rawDataPath"] = raw_path
            job.collector_results.append(failed_result)
            store.record_source_failure(name, job.job_id, message, threshold=SOURCE_FAILURE_THRESHOLD)
            print(f"{name}: failed: {message}", file=sys.stderr)
    return total


def _validate_collector_result(name: str, limit: int, result) -> None:
    if limit > 0 and not result.games:
        raise RuntimeError(f"{name} returned 0 games from {result.fetched_url}; parser may need review")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run official source release sync.")
    parser.add_argument(
        "--collectors",
        default=",".join(DEFAULT_COLLECTORS),
        help="Comma-separated collector names. Default: steam,nintendo,playstation",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-config", action="store_true", help="Validate required environment settings.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv or sys.argv[1:])
    if args.check_config:
        return check_config()
    collectors = [item.strip() for item in args.collectors.split(",") if item.strip()]
    result = run_sync(collectors, limit=args.limit, dry_run=args.dry_run)
    print(
        "official-release-sync completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
