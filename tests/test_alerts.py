from __future__ import annotations

import unittest

from core.neon import NeonStore
from tracked.common import record_refresh_source_status


class AlertRecordingTest(unittest.TestCase):
    def test_source_failure_creates_warning_alert_before_threshold(self) -> None:
        cursor = FakeCursor(failure_count=1)
        store = _store_with_cursor(cursor)

        store.record_source_failure("steam", "job-1", "boom", threshold=3)

        self.assertEqual(cursor.queries[1][1][1], "warning")
        self.assertIn("source_failure", cursor.queries[1][0])

    def test_source_failure_becomes_critical_at_threshold(self) -> None:
        cursor = FakeCursor(failure_count=3)
        store = _store_with_cursor(cursor)

        store.record_source_failure("steam", "job-1", "boom", threshold=3)

        self.assertEqual(cursor.queries[1][1][1], "critical")

    def test_source_success_resolves_open_failure_alert(self) -> None:
        cursor = FakeCursor(failure_count=0)
        store = _store_with_cursor(cursor)

        store.record_source_success("steam")

        self.assertIn("status = 'resolved'", cursor.queries[0][0])
        self.assertEqual(cursor.queries[0][1], ("steam",))

    def test_tracked_failed_status_records_source_failure(self) -> None:
        store = FakeRefreshStore()

        record_refresh_source_status(store, "steam", "job-1", "failed", "boom")

        self.assertEqual(store.failures, [("steam", "job-1", "boom")])
        self.assertEqual(store.successes, [])

    def test_tracked_partial_status_records_source_success(self) -> None:
        store = FakeRefreshStore()

        record_refresh_source_status(store, "steam", "job-1", "partial_success", "row failed")

        self.assertEqual(store.successes, ["steam"])
        self.assertEqual(store.failures, [])


class FakeCursor:
    def __init__(self, failure_count: int):
        self.failure_count = failure_count
        self.queries: list[tuple[str, tuple | None]] = []
        self._next_row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.queries.append((query, params))
        if "RETURNING id, name, consecutive_failures" in query:
            self._next_row = ("source-1", "Steam", self.failure_count)

    def fetchone(self):
        return self._next_row


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor


class FakeRefreshStore:
    def __init__(self):
        self.failures: list[tuple[str, str | None, str]] = []
        self.successes: list[str] = []

    def record_source_failure(self, source_slug: str, data_job_id: str | None, error_message: str, threshold: int = 3) -> None:
        self.failures.append((source_slug, data_job_id, error_message))

    def record_source_success(self, source_slug: str) -> None:
        self.successes.append(source_slug)


def _store_with_cursor(cursor: FakeCursor) -> NeonStore:
    store = NeonStore("postgres://example")
    store.conn = FakeConn(cursor)
    return store


if __name__ == "__main__":
    unittest.main()
