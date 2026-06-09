from __future__ import annotations

import unittest

from core.models import CollectedGame, StoreLink
from tracked.gog_tracked_refresh import _matching_game


class GogRefreshHelpersTest(unittest.TestCase):
    def test_matching_game_prefers_product_id(self) -> None:
        game = CollectedGame(
            title="Example GOG Game",
            source_slug="gog",
            source_url="https://www.gog.com/en/game/example_gog_game",
            platform_slugs=["pc", "gog"],
            external_ids={"gogProductId": "1234567890", "productId": "1234567890"},
            store_links=[
                StoreLink(
                    id="gog:1234567890",
                    store_name="gog",
                    url="https://www.gog.com/en/game/example_gog_game",
                    product_id="1234567890",
                    sku_id="example_gog_game",
                )
            ],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": "1234567890",
                "sku_id": "wrong",
                "store_url": "https://www.gog.com/en/game/other",
            },
        )

        self.assertIs(matched, game)

    def test_matching_game_falls_back_to_store_url(self) -> None:
        game = CollectedGame(
            title="Example GOG Game",
            source_slug="gog",
            source_url="https://www.gog.com/en/game/example_gog_game",
            platform_slugs=["pc", "gog"],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": None,
                "sku_id": None,
                "store_url": "https://www.gog.com/en/game/example_gog_game/",
            },
        )

        self.assertIs(matched, game)


if __name__ == "__main__":
    unittest.main()
