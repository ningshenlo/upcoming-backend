from __future__ import annotations

from dataclasses import dataclass, field

from .neon import NeonStore


@dataclass
class JobRun:
    job_id: str | None
    raw_data_path: str | None = None
    raw_data_paths: list[str] | None = None
    processed_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    collector_results: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def start_job(store: NeonStore | None, job_type: str, params: dict | None = None) -> JobRun:
    if store is None:
        return JobRun(job_id=None)
    return JobRun(job_id=store.create_data_job(job_type, params=params))


def resolve_job_status(run: JobRun) -> str:
    if run.error_count == 0:
        return "success"
    if run.processed_count > 0 or run.updated_count > 0 or run.created_count > 0:
        return "partial_success"
    return "failed"


def finish_job(store: NeonStore | None, run: JobRun, status: str | None = None, error_message: str | None = None) -> None:
    if store is None or run.job_id is None:
        return
    resolved_status = status or resolve_job_status(run)
    metadata = {
        "rawDataPaths": run.raw_data_paths or [],
        "collectorResults": run.collector_results,
        "failedCount": run.error_count,
    }
    if run.errors:
        metadata["errors"] = run.errors
    store.finish_data_job(
        job_id=run.job_id,
        status=resolved_status,
        raw_data_path=run.raw_data_path,
        raw_data_paths=run.raw_data_paths,
        processed_count=run.processed_count,
        created_count=run.created_count,
        updated_count=run.updated_count,
        skipped_count=run.skipped_count,
        error_count=run.error_count,
        error_message=error_message,
        metadata=metadata,
    )
