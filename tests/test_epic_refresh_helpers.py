from __future__ import annotations

import unittest

from core.models import CollectedGame, StoreLink
from epic_tracked_refresh import _matching_game, _offer_identity


class EpicRefreshHelpersTest(unittest.TestCase):
    def test_offer_identity_reads_metadata_and_sku(self) -> None:
        namespace, offer_id = _offer_identity(
            {
                "product_id": "product-1",
                "sku_id": "offer-1",
                "metadata": {"namespace": "namespace-1"},
            }
        )

        self.assertEqual(namespace, "namespace-1")
        self.assertEqual(offer_id, "offer-1")

    def test_offer_identity_falls_back_to_compound_product_id(self) -> None:
        namespace, offer_id = _offer_identity(
            {
                "product_id": "namespace-1:offer-1",
                "sku_id": None,
                "metadata": {},
            }
        )

        self.assertEqual(namespace, "namespace-1")
        self.assertEqual(offer_id, "offer-1")

    def test_matching_game_prefers_product_id(self) -> None:
        game = CollectedGame(
            title="Example Epic Game",
            source_slug="epic",
            source_url="https://store.epicgames.com/en-US/p/example-epic-game",
            platform_slugs=["pc", "epic-games-store"],
            external_ids={
                "productId": "product-1",
                "epicProductId": "product-1",
                "epicOfferId": "offer-1",
                "epicNamespace": "namespace-1",
            },
            store_links=[
                StoreLink(
                    id="epic_games_store:product-1",
                    store_name="epic_games_store",
                    url="https://store.epicgames.com/en-US/p/example-epic-game",
                    product_id="product-1",
                    sku_id="offer-1",
                )
            ],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": "product-1",
                "sku_id": "wrong",
                "metadata": {"namespace": "wrong"},
                "store_url": "https://store.epicgames.com/en-US/p/other",
            },
        )

        self.assertIs(matched, game)

    def test_matching_game_falls_back_to_store_url(self) -> None:
        game = CollectedGame(
            title="Example Epic Game",
            source_slug="epic",
            source_url="https://store.epicgames.com/en-US/p/example-epic-game",
            platform_slugs=["pc", "epic-games-store"],
        )

        matched = _matching_game(
            [game],
            {
                "product_id": None,
                "sku_id": None,
                "metadata": {},
                "store_url": "https://store.epicgames.com/en-US/p/example-epic-game/",
            },
        )

        self.assertIs(matched, game)


if __name__ == "__main__":
    unittest.main()
