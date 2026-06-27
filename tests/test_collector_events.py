from __future__ import annotations

import unittest

from collectors.playstation import _events_from_store_links
from collectors.steam import apply_appdetails
from core.models import CollectedGame, StoreLink


class CollectorEventsTest(unittest.TestCase):
    def test_steam_appdetails_adds_demo_event(self) -> None:
        game = CollectedGame(
            title="Example Game",
            source_slug="steam",
            source_url="https://store.steampowered.com/app/123/Example_Game/",
            platform_slugs=["pc", "steam"],
            external_ids={"steamAppId": 123},
        )
        enriched = apply_appdetails(
            game,
            {
                "123": {
                    "success": True,
                    "data": {
                        "type": "game",
                        "steam_appid": 123,
                        "name": "Example Game",
                        "release_date": {"coming_soon": True, "date": "Q3 2026"},
                        "demos": [{"appid": 456}],
                    },
                }
            },
        )

        self.assertIsNotNone(enriched)
        assert enriched is not None
        self.assertEqual(enriched.release_date, "2026-07-01")
        self.assertEqual(enriched.date_accuracy, "quarter")
        self.assertEqual([event.event_type for event in enriched.events], ["demo"])

    def test_steam_appdetails_can_parse_released_price_for_tracked_refresh(self) -> None:
        game = CollectedGame(
            title="Example Game",
            source_slug="steam",
            source_url="https://store.steampowered.com/app/123/Example_Game/",
            platform_slugs=["pc", "steam"],
            external_ids={"steamAppId": 123},
        )
        payload = {
            "123": {
                "success": True,
                "data": {
                    "type": "game",
                    "steam_appid": 123,
                    "name": "Example Game",
                    "release_date": {"coming_soon": False, "date": "Jun 26, 2026"},
                    "price_overview": {
                        "final": 799,
                        "final_formatted": "$7.99",
                        "currency": "USD",
                    },
                },
            }
        }

        self.assertIsNone(apply_appdetails(game, payload))
        enriched = apply_appdetails(game, payload, allow_released=True)

        self.assertIsNotNone(enriched)
        assert enriched is not None
        self.assertEqual(enriched.store_links[0].price_text, "$7.99")
        self.assertEqual(enriched.store_links[0].price, 7.99)
        self.assertEqual(enriched.store_links[0].currency, "USD")

    def test_playstation_store_links_add_demo_event(self) -> None:
        game = CollectedGame(
            title="Example Game",
            source_slug="playstation",
            source_url="https://store.playstation.com/en-us/concept/100000",
            platform_slugs=["ps5"],
        )
        events = _events_from_store_links(
            game,
            [
                StoreLink(
                    id="playstation_store:UP0000-PPSA00000_00-EXAMPLE",
                    store_name="playstation_store",
                    url="https://store.playstation.com/en-us/product/UP0000-PPSA00000_00-EXAMPLE",
                    platform_slugs=["ps5"],
                    product_id="UP0000-PPSA00000_00-EXAMPLE",
                    demo_available=True,
                )
            ],
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "demo")
        self.assertEqual(events[0].platform_slugs, ["ps5"])


if __name__ == "__main__":
    unittest.main()
