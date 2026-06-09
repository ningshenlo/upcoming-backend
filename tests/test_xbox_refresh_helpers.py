from __future__ import annotations

import unittest

from core.models import CollectedGame, StoreLink
from xbox_tracked_refresh import _matching_game


class XboxRefreshHelpersTest(unittest.TestCase):
    def test_matching_game_prefers_product_id(self) -> None:
        game = CollectedGame(
            title="Example Xbox Game",
            source_slug="xbox",
            source_url="https://www.xbox.com/en-US/games/store/example-xbox-game/9TEST123456",
            platform_slugs=["xbox-series"],
            external_ids={"xboxProductId": "9TEST123456"},
            store_links=[
                StoreLink(
                    id="xbox_store:9TEST123456",
                    store_name="xbox_store",
                    url="https://www.xbox.com/en-US/games/store/example-xbox-game/9TEST123456",
                    product_id="9TEST123456",
                )
            ],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": "9TEST123456",
                "sku_id": "wrong",
                "store_url": "https://www.xbox.com/en-US/games/store/other/9OTHER",
            },
        )

        self.assertIs(matched, game)

    def test_matching_game_falls_back_to_store_url(self) -> None:
        game = CollectedGame(
            title="Example Xbox Game",
            source_slug="xbox",
            source_url="https://www.xbox.com/en-US/games/store/example-xbox-game/9TEST123456",
            platform_slugs=["xbox-series"],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": None,
                "sku_id": None,
                "store_url": "https://www.xbox.com/en-US/games/store/example-xbox-game/9TEST123456/",
            },
        )

        self.assertIs(matched, game)


if __name__ == "__main__":
    unittest.main()
