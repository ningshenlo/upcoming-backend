from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.neon import NeonStore
from core.models import CollectedEvent


class NeonEventLogicTest(unittest.TestCase):
    def test_more_precise_date_replaces_approximate_date(self) -> None:
        store = NeonStore("postgres://example")
        existing = datetime(2026, 1, 1, tzinfo=timezone.utc)

        self.assertEqual(
            store._next_event_date(existing, "year", "2026-06-12", "exact"),
            "2026-06-12",
        )

    def test_less_precise_date_does_not_replace_exact_date(self) -> None:
        store = NeonStore("postgres://example")
        existing = datetime(2026, 6, 12, tzinfo=timezone.utc)

        self.assertIsNone(store._next_event_date(existing, "exact", "2026-01-01", "year"))

    def test_best_accuracy_keeps_more_precise_value(self) -> None:
        store = NeonStore("postgres://example")

        self.assertEqual(store._best_date_accuracy("month", "exact"), "exact")
        self.assertEqual(store._best_date_accuracy("exact", "year"), "exact")

    def test_event_fact_type_mapping(self) -> None:
        store = NeonStore("postgres://example")

        self.assertEqual(store._event_fact_type(CollectedEvent(event_type="release")), "release_date")
        self.assertEqual(store._event_fact_type(CollectedEvent(event_type="demo")), "demo_availability")
        self.assertEqual(store._event_fact_type(CollectedEvent(event_type="beta")), "event")


if __name__ == "__main__":
    unittest.main()
