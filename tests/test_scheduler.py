from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scheduler import LastJobRun, ScheduledJob, build_jobs, is_job_due


class SchedulerConfigTest(unittest.TestCase):
    def test_build_jobs_reads_env_overrides(self) -> None:
        jobs = {job.key: job for job in build_jobs(
            {
                "OFFICIAL_RELEASE_SYNC_INTERVAL_MINUTES": "30",
                "OFFICIAL_RELEASE_LIMIT": "12",
                "STEAM_TRACKED_REFRESH_ENABLED": "0",
                "STEAM_TRACKED_REFRESH_INTERVAL_MINUTES": "15",
                "STEAM_TRACKED_REFRESH_LIMIT": "7",
                "DFS": "example-key",
                "DATAFORSEO_YOUTUBE_TASK_GET_LIMIT": "9",
            },
            python_executable="python",
        )}

        self.assertEqual(jobs["official-release-sync"].interval_minutes, 30)
        self.assertIn("12", jobs["official-release-sync"].command)
        self.assertFalse(jobs["steam-tracked-refresh"].enabled)
        self.assertEqual(jobs["steam-tracked-refresh"].interval_minutes, 15)
        self.assertEqual(jobs["steam-tracked-refresh"].command, ("python", "tracked/steam_tracked_refresh.py", "--limit", "7"))
        self.assertTrue(jobs["dataforseo-youtube-task-get"].enabled)
        self.assertEqual(jobs["dataforseo-youtube-task-get"].interval_minutes, 5)
        self.assertEqual(
            jobs["dataforseo-youtube-task-get"].command,
            ("python", "dataforseo_youtube_task_get.py", "--limit", "9"),
        )

    def test_build_jobs_uses_youtube_hot_tracker_defaults(self) -> None:
        jobs = {job.key: job for job in build_jobs({}, python_executable="python")}

        self.assertEqual(jobs["hot-tracker"].interval_minutes, 360)
        self.assertIn("2000", jobs["hot-tracker"].command)
        self.assertIn("5000", jobs["hot-tracker"].command)

    def test_job_not_due_before_interval(self) -> None:
        now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
        job = _job(interval_minutes=60)
        last_run = LastJobRun(
            status="success",
            started_at=now - timedelta(minutes=31),
            completed_at=now - timedelta(minutes=30),
            created_at=now - timedelta(minutes=31),
        )

        self.assertFalse(is_job_due(job, last_run, now, timeout_minutes=120))

    def test_job_due_after_interval(self) -> None:
        now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
        job = _job(interval_minutes=60)
        last_run = LastJobRun(
            status="success",
            started_at=now - timedelta(minutes=91),
            completed_at=now - timedelta(minutes=90),
            created_at=now - timedelta(minutes=91),
        )

        self.assertTrue(is_job_due(job, last_run, now, timeout_minutes=120))

    def test_running_job_waits_until_timeout(self) -> None:
        now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
        job = _job(interval_minutes=60)
        last_run = LastJobRun(
            status="running",
            started_at=now - timedelta(minutes=30),
            completed_at=None,
            created_at=now - timedelta(minutes=30),
        )

        self.assertFalse(is_job_due(job, last_run, now, timeout_minutes=120))

    def test_stale_running_job_is_due_after_timeout(self) -> None:
        now = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
        job = _job(interval_minutes=60)
        last_run = LastJobRun(
            status="running",
            started_at=now - timedelta(minutes=121),
            completed_at=None,
            created_at=now - timedelta(minutes=121),
        )

        self.assertTrue(is_job_due(job, last_run, now, timeout_minutes=120))


def _job(interval_minutes: int) -> ScheduledJob:
    return ScheduledJob(
        key="example",
        job_type="official_release_sync",
        mode=None,
        command=("python", "example.py"),
        interval_minutes=interval_minutes,
        enabled=True,
    )


if __name__ == "__main__":
    unittest.main()
