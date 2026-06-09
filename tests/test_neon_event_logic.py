from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.neon import NeonStore
from core.models import CollectedEvent, CollectedGame, StoreLink


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

    def test_data_completeness_uses_percent_scale(self) -> None:
        store = NeonStore("postgres://example")
        game = CollectedGame(
            title="Example Game",
            source_slug="steam",
            source_url="https://store.steampowered.com/app/123/Example_Game/",
            platform_slugs=["pc", "steam"],
            release_date="2026-06-12",
            date_accuracy="exact",
            cover_image_url="https://cdn.example/cover.jpg",
            description="Example description",
            publishers=["Example Publisher"],
            store_links=[
                StoreLink(
                    store_name="steam",
                    url="https://store.steampowered.com/app/123/Example_Game/",
                )
            ],
            events=[CollectedEvent(event_type="demo")],
        )

        self.assertEqual(store._data_completeness(game), 100.0)

    def test_game_update_changes_detect_key_store_changes(self) -> None:
        store = NeonStore("postgres://example")
        game = CollectedGame(
            title="Example Game",
            source_slug="steam",
            source_url="https://store.steampowered.com/app/123/Example_Game/",
            platform_slugs=["pc", "steam"],
            release_date="2026-08-01",
            date_accuracy="month",
            publishers=["Example Publisher"],
            store_links=[
                StoreLink(
                    id="steam:123",
                    store_name="steam",
                    url="https://store.steampowered.com/app/123/Example_Game/",
                    product_id="123",
                    price_text="$29.99",
                    price=29.99,
                    currency="USD",
                    demo_available=True,
                    metadata={"tags": ["Action", "RPG"]},
                )
            ],
        )
        previous = {
            "release": {"date": "2026-07-01", "dateAccuracy": "month"},
            "stores": {
                "steam:123": {
                    "priceText": None,
                    "price": None,
                    "currency": None,
                    "demoAvailable": False,
                    "tags": [],
                }
            },
            "publishers": [],
            "developers": [],
        }

        update_types = [change["update_type"] for change in store._game_update_changes(previous, game)]

        self.assertIn("release_date_changed", update_types)
        self.assertIn("demo_available", update_types)
        self.assertIn("price_available", update_types)
        self.assertIn("metadata_enriched", update_types)
        self.assertIn("company_changed", update_types)

    def test_game_update_changes_detect_release_accuracy_improvement(self) -> None:
        store = NeonStore("postgres://example")
        game = CollectedGame(
            title="Example Game",
            source_slug="steam",
            source_url="https://store.steampowered.com/app/123/Example_Game/",
            platform_slugs=["pc", "steam"],
            release_date="2026-01-01",
            date_accuracy="exact",
        )
        previous = {
            "release": {"date": "2026-01-01", "dateAccuracy": "year"},
            "stores": {},
            "publishers": [],
            "developers": [],
        }

        update_types = [change["update_type"] for change in store._game_update_changes(previous, game)]

        self.assertEqual(update_types, ["release_date_confirmed"])


if __name__ == "__main__":
    unittest.main()
