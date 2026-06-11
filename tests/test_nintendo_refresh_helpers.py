from __future__ import annotations

import unittest

from core.models import CollectedGame
from tracked.nintendo_tracked_refresh import _matching_game


class NintendoRefreshHelpersTest(unittest.TestCase):
    def test_matching_game_prefers_nsuid(self) -> None:
        game = CollectedGame(
            title="Example Game",
            source_slug="nintendo",
            source_url="https://www.nintendo.com/us/store/products/example-game-switch/",
            platform_slugs=["nintendo-switch"],
            external_ids={"nintendoNsuid": "70010000000000", "nintendoSku": "7100000000"},
        )

        matched = _matching_game(
            [game],
            {
                "nsuid": "70010000000000",
                "sku_id": "wrong",
                "store_url": "https://www.nintendo.com/us/store/products/other/",
            },
        )

        self.assertIs(matched, game)

    def test_matching_game_falls_back_to_url(self) -> None:
        game = CollectedGame(
            title="Example Game",
            source_slug="nintendo",
            source_url="https://www.nintendo.com/us/store/products/example-game-switch/",
            platform_slugs=["nintendo-switch"],
        )

        matched = _matching_game(
            [game],
            {
                "nsuid": None,
                "sku_id": None,
                "store_url": "https://www.nintendo.com/us/store/products/example-game-switch",
            },
        )

        self.assertIs(matched, game)


if __name__ == "__main__":
    unittest.main()
