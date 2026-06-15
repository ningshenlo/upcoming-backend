from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

from core.config import load_settings
from core.neon import NeonStore


DEFAULT_COLLECTORS = "steam,nintendo,playstation,xbox,epic,gog"
DEFAULT_HOT_TRACKER_CHANNELS = "youtube"


@dataclass(frozen=True)
class ScheduledJob:
    key: str
    job_type: str
    mode: str | None
    command: tuple[str, ...]
    interval_minutes: int
    enabled: bool


@dataclass(frozen=True)
class LastJobRun:
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None


@dataclass(frozen=True)
class JobExecution:
    job: ScheduledJob
    due: bool
    ran: bool
    exit_code: int | None = None
    error_message: str | None = None


def build_jobs(env: Mapping[str, str] | None = None, python_executable: str | None = None) -> list[ScheduledJob]:
    values = os.environ if env is None else env
    python = python_executable or sys.executable
    return [
        ScheduledJob(
            key="official-release-sync",
            job_type="official_release_sync",
            mode=None,
            command=(
                python,
                "official_release_sync.py",
                "--collectors",
                values.get("OFFICIAL_RELEASE_COLLECTORS", DEFAULT_COLLECTORS),
                "--limit",
                str(_int_env(values, "OFFICIAL_RELEASE_LIMIT", 50)),
            ),
            interval_minutes=_int_env(values, "OFFICIAL_RELEASE_SYNC_INTERVAL_MINUTES", 720),
            enabled=_bool_env(values, "OFFICIAL_RELEASE_SYNC_ENABLED", True),
        ),
        _tracked_job(values, python, "steam", "STEAM", 360),
        _tracked_job(values, python, "playstation", "PLAYSTATION", 360),
        _tracked_job(values, python, "nintendo", "NINTENDO", 360),
        _tracked_job(values, python, "xbox", "XBOX", 360),
        _tracked_job(values, python, "epic", "EPIC", 360),
        _tracked_job(values, python, "gog", "GOG", 360),
        ScheduledJob(
            key="hot-tracker",
            job_type="youtube_track",
            mode="hot_tracker",
            command=(
                python,
                "hot_tracker.py",
                "--channels",
                values.get("HOT_TRACKER_CHANNELS", DEFAULT_HOT_TRACKER_CHANNELS),
                "--discover-limit",
                str(_int_env(values, "HOT_TRACKER_DISCOVERY_LIMIT", 2000)),
                "--refresh-limit",
                str(_int_env(values, "HOT_TRACKER_REFRESH_LIMIT", 5000)),
                "--rediscovery-days",
                str(_int_env(values, "HOT_TRACKER_REDISCOVERY_DAYS", 7)),
            ),
            interval_minutes=_int_env(values, "HOT_TRACKER_INTERVAL_MINUTES", 360),
            enabled=_bool_env(values, "HOT_TRACKER_ENABLED", True),
        ),
        ScheduledJob(
            key="dataforseo-youtube-task-get",
            job_type="youtube_track",
            mode="dataforseo_youtube_task_get",
            command=(
                python,
                "dataforseo_youtube_task_get.py",
                "--limit",
                str(_int_env(values, "DATAFORSEO_YOUTUBE_TASK_GET_LIMIT", 50)),
            ),
            interval_minutes=_int_env(values, "DATAFORSEO_YOUTUBE_TASK_GET_INTERVAL_MINUTES", 5),
            enabled=_bool_env(
                values,
                "DATAFORSEO_YOUTUBE_TASK_GET_ENABLED",
                bool(values.get("DFS") or values.get("DATAFORSEO_API_KEY")),
            ),
        ),
    ]


def _tracked_job(
    env: Mapping[str, str],
    python: str,
    source: str,
    prefix: str,
    default_interval_minutes: int,
) -> ScheduledJob:
    return ScheduledJob(
        key=f"{source}-tracked-refresh",
        job_type="official_release_sync",
        mode=f"{source}_tracked_refresh",
        command=(
            python,
            f"tracked/{source}_tracked_refresh.py",
            "--limit",
            str(_int_env(env, f"{prefix}_TRACKED_REFRESH_LIMIT", 80)),
        ),
        interval_minutes=_int_env(env, f"{prefix}_TRACKED_REFRESH_INTERVAL_MINUTES", default_interval_minutes),
        enabled=_bool_env(env, f"{prefix}_TRACKED_REFRESH_ENABLED", True),
    )


def is_job_due(
    job: ScheduledJob,
    last_run: LastJobRun | None,
    now: datetime,
    timeout_minutes: int,
) -> bool:
    if not job.enabled or job.interval_minutes <= 0:
        return False
    if last_run is None:
        return True

    reference_at = last_run.completed_at or last_run.started_at or last_run.created_at
    if reference_at is None:
        return True
    reference_at = _as_aware_utc(reference_at)

    if last_run.status == "running":
        return now - reference_at >= timedelta(minutes=timeout_minutes)
    return now - reference_at >= timedelta(minutes=job.interval_minutes)


def run_once(
    jobs: Sequence[ScheduledJob],
    timeout_minutes: int,
    dry_run: bool = False,
) -> list[JobExecution]:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for scheduler")

    executions: list[JobExecution] = []
    now = datetime.now(timezone.utc)
    with NeonStore(settings.database_url) as store:
        timed_out = store.fail_stale_running_jobs(timeout_hours=max(1, (timeout_minutes + 59) // 60))
        if timed_out:
            store.conn.commit()
        for job in jobs:
            last_run = _latest_job_run(store, job)
            due = is_job_due(job, last_run, now, timeout_minutes)
            if not due:
                executions.append(JobExecution(job=job, due=False, ran=False))
                continue
            if dry_run:
                executions.append(JobExecution(job=job, due=True, ran=False))
                continue
            executions.append(_run_job(store, job, timeout_minutes))
    return executions


def _latest_job_run(store: NeonStore, job: ScheduledJob) -> LastJobRun | None:
    assert store.conn is not None
    with store.conn.cursor() as cur:
        if job.mode is None:
            cur.execute(
                """
                SELECT status, started_at, completed_at, created_at
                FROM data_jobs
                WHERE job_type = %s
                  AND COALESCE(params->>'mode', '') = ''
                ORDER BY COALESCE(started_at, created_at) DESC NULLS LAST
                LIMIT 1
                """,
                (job.job_type,),
            )
        else:
            cur.execute(
                """
                SELECT status, started_at, completed_at, created_at
                FROM data_jobs
                WHERE job_type = %s
                  AND params->>'mode' = %s
                ORDER BY COALESCE(started_at, created_at) DESC NULLS LAST
                LIMIT 1
                """,
                (job.job_type, job.mode),
            )
        row = cur.fetchone()
    if not row:
        return None
    return LastJobRun(status=row[0], started_at=row[1], completed_at=row[2], created_at=row[3])


def _run_job(store: NeonStore, job: ScheduledJob, timeout_minutes: int) -> JobExecution:
    print(f"scheduler: running {job.key}: {' '.join(job.command)} | 调度器：开始执行任务 {job.key}", flush=True)
    started_at = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            job.command,
            check=False,
            timeout=timeout_minutes * 60,
            stderr=subprocess.STDOUT,
        )
    except subprocess.TimeoutExpired as exc:
        message = f"Scheduled job timed out after {timeout_minutes} minutes."
        _record_scheduler_failure(store, job.key, message)
        return JobExecution(job=job, due=True, ran=True, exit_code=None, error_message=str(exc))
    except OSError as exc:
        message = f"Scheduled job could not start: {exc}"
        _record_scheduler_failure(store, job.key, message)
        return JobExecution(job=job, due=True, ran=False, exit_code=None, error_message=message)

    if completed.returncode == 0:
        _record_scheduler_success(store, job.key)
        return JobExecution(job=job, due=True, ran=True, exit_code=0)

    message = f"Scheduled job exited with code {completed.returncode}."
    if _job_logged_since(store, job, started_at):
        _record_scheduler_success(store, job.key)
        return JobExecution(job=job, due=True, ran=True, exit_code=completed.returncode, error_message=message)
    _record_scheduler_failure(store, job.key, message)
    return JobExecution(job=job, due=True, ran=True, exit_code=completed.returncode, error_message=message)


def _job_logged_since(store: NeonStore, job: ScheduledJob, started_at: datetime) -> bool:
    try:
        store.reconnect()
        assert store.conn is not None
        with store.conn.cursor() as cur:
            if job.mode is None:
                cur.execute(
                    """
                    SELECT 1
                    FROM data_jobs
                    WHERE job_type = %s
                      AND COALESCE(params->>'mode', '') = ''
                      AND COALESCE(started_at, created_at) >= %s
                    LIMIT 1
                    """,
                    (job.job_type, started_at),
                )
            else:
                cur.execute(
                    """
                    SELECT 1
                    FROM data_jobs
                    WHERE job_type = %s
                      AND params->>'mode' = %s
                      AND COALESCE(started_at, created_at) >= %s
                    LIMIT 1
                    """,
                    (job.job_type, job.mode, started_at),
                )
            return cur.fetchone() is not None
    except Exception as exc:
        print(f"scheduler: failed to inspect data_jobs for {job.key}: {exc}", file=sys.stderr, flush=True)
        return False


def _record_scheduler_failure(store: NeonStore, job_key: str, message: str) -> None:
    try:
        store.reconnect()
        store.record_scheduler_failure(job_key, message)
        store.conn.commit()
    except Exception as exc:
        print(f"scheduler: failed to record scheduler alert for {job_key}: {exc}", file=sys.stderr, flush=True)


def _record_scheduler_success(store: NeonStore, job_key: str) -> None:
    try:
        store.reconnect()
        store.record_scheduler_success(job_key)
        store.conn.commit()
    except Exception as exc:
        print(f"scheduler: failed to resolve scheduler alert for {job_key}: {exc}", file=sys.stderr, flush=True)


def run_loop(jobs: Sequence[ScheduledJob], tick_seconds: int, timeout_minutes: int, dry_run: bool = False) -> None:
    while True:
        executions = run_once(jobs, timeout_minutes=timeout_minutes, dry_run=dry_run)
        _print_executions(executions)
        time.sleep(max(1, tick_seconds))


def _print_executions(executions: Sequence[JobExecution]) -> None:
    for execution in executions:
        if not execution.due:
            print(f"scheduler: skipped {execution.job.key}; not due | 调度器：跳过任务 {execution.job.key}，尚未到执行时间", flush=True)
            continue
        if not execution.ran:
            print(f"scheduler: due {execution.job.key}; dry-run only | 调度器：任务 {execution.job.key} 已到期，当前为演练模式", flush=True)
            continue
        if execution.exit_code == 0:
            print(f"scheduler: completed {execution.job.key} | 调度器：任务 {execution.job.key} 执行完成", flush=True)
        else:
            print(f"scheduler: failed {execution.job.key}: {execution.error_message} | 调度器：任务 {execution.job.key} 执行失败", file=sys.stderr, flush=True)


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled upcoming-games scraper jobs.")
    parser.add_argument("--once", action="store_true", help="Run one scheduler tick and exit.")
    parser.add_argument("--list-jobs", action="store_true", help="Print configured jobs and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Do not start due jobs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    jobs = build_jobs()
    if args.list_jobs:
        for job in jobs:
            enabled = "enabled" if job.enabled else "disabled"
            print(f"{job.key}: {enabled}, every {job.interval_minutes} minutes, command={' '.join(job.command)}")
        return 0

    tick_seconds = _int_env(os.environ, "SCHEDULER_TICK_SECONDS", 60)
    timeout_minutes = _int_env(os.environ, "SCHEDULER_JOB_TIMEOUT_MINUTES", 120)
    if args.once:
        executions = run_once(jobs, timeout_minutes=timeout_minutes, dry_run=args.dry_run)
        _print_executions(executions)
        return 1 if any(item.error_message for item in executions) else 0

    run_loop(jobs, tick_seconds=tick_seconds, timeout_minutes=timeout_minutes, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
