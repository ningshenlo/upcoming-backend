from __future__ import annotations

from core.neon import NeonStore


def record_refresh_source_status(
    store: NeonStore,
    source_slug: str,
    data_job_id: str | None,
    status: str,
    error_message: str | None,
) -> None:
    if status == "failed":
        store.record_source_failure(source_slug, data_job_id, error_message or "Tracked refresh failed")
    else:
        store.record_source_success(source_slug)
