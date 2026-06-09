from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from collectors.igdb import collect
from core.config import load_settings
from core.job_logger import finish_job, resolve_job_status, start_job
from core.neon import NeonStore
from core.r2 import RawArchive


SOURCE_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class DiscoveryRunResult:
    status: str
    processed_count: int
    failed_count: int


def run_sync(limit: int, dry_run: bool) -> DiscoveryRunResult:
    settings = load_settings()
    archive = RawArchive(settings)
    store_ctx = NeonStore(settings.database_url) if settings.database_url and not dry_run else None

    if store_ctx is None:
        result = collect(limit=limit, user_agent=settings.user_agent)
        raw_path = archive.write_json(result.source_slug, result.raw_payload)
        print(f"igdb: {len(result.games)} candidates, raw={raw_path}")
        return DiscoveryRunResult(status="success", processed_count=len(result.games), failed_count=0)

    with store_ctx as store:
        job = start_job(
            store,
            "igdb_discovery_sync",
            {
                "collector": "igdb",
                "limit": limit,
                "dryRun": dry_run,
                "startedAt": datetime.now(timezone.utc).isoformat(),
                "policy": "candidate_discovery_only",
            },
        )
        try:
            result = collect(limit=limit, user_agent=settings.user_agent)
            raw_path = archive.write_json(result.source_slug, result.raw_payload)
            skipped = isinstance(result.raw_payload, dict) and result.raw_payload.get("status") == "skipped"
            job.raw_data_path = raw_path
            job.raw_data_paths = [raw_path]
            job.processed_count = len(result.games)
            for game in result.games:
                store.upsert_discovery_candidate(game, raw_data_path=raw_path, data_job_id=job.job_id)
                job.updated_count += 1
            if not skipped:
                store.record_source_success(result.source_slug)
            job.collector_results.append(
                {
                    "collector": "igdb",
                    "status": "skipped" if skipped else "success",
                    "processedCount": len(result.games),
                    "rawDataPath": raw_path,
                }
            )
        except Exception as exc:
            message = str(exc)
            job.error_count += 1
            job.errors.append({"collector": "igdb", "message": message})
            job.collector_results.append({"collector": "igdb", "status": "failed", "error": message})
            store.record_source_failure("igdb", job.job_id, message, threshold=SOURCE_FAILURE_THRESHOLD)
            finish_job(store, job, "failed", error_message=message)
            raise

        status = resolve_job_status(job)
        finish_job(store, job, status)
        return DiscoveryRunResult(status=status, processed_count=job.processed_count, failed_count=job.error_count)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run IGDB candidate discovery sync.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv or sys.argv[1:])
    result = run_sync(limit=args.limit, dry_run=args.dry_run)
    print(
        "igdb-discovery-sync completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
