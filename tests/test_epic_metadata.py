from __future__ import annotations

import unittest

from collectors.epic import parse_search_payload


class EpicMetadataTest(unittest.TestCase):
    def test_search_payload_maps_to_game_and_store_link(self) -> None:
        games = parse_search_payload(
            {
                "data": {
                    "Catalog": {
                        "searchStore": {
                            "elements": [
                                {
                                    "title": "Example Epic Game",
                                    "id": "offer-1",
                                    "namespace": "namespace-1",
                                    "description": "<p>A test game.</p>",
                                    "effectiveDate": "2027-06-12T15:00:00.000Z",
                                    "isCodeRedemptionOnly": False,
                                    "keyImages": [
                                        {"type": "OfferImageTall", "url": "https://example.com/tall.jpg"},
                                        {"type": "OfferImageWide", "url": "https://example.com/wide.jpg"},
                                        {"type": "Screenshot", "url": "https://example.com/screen.jpg"},
                                    ],
                                    "seller": {"id": "seller-1", "name": "Example Seller"},
                                    "productSlug": None,
                                    "urlSlug": "fallback-slug",
                                    "tags": [{"id": "1296"}, {"id": "1393"}],
                                    "customAttributes": [{"key": "com.epicgames.app.productSlug", "value": "example"}],
                                    "categories": [{"path": "games"}, {"path": "games/edition/base"}],
                                    "catalogNs": {
                                        "mappings": [
                                            {
                                                "pageSlug": "example-epic-game",
                                                "pageType": "productHome",
                                                "productId": "product-1",
                                                "sandboxId": "namespace-1",
                                            }
                                        ]
                                    },
                                    "offerMappings": [],
                                    "developerDisplayName": "Example Studio",
                                    "publisherDisplayName": "Example Publisher",
                                    "price": {
                                        "totalPrice": {
                                            "discountPrice": 4999,
                                            "originalPrice": 5999,
                                            "currencyCode": "USD",
                                            "currencyInfo": {"decimals": 2},
                                            "fmtPrice": {"discountPrice": "$49.99"},
                                        }
                                    },
                                    "prePurchase": True,
                                    "releaseDate": "2027-06-12T15:00:00.000Z",
                                    "pcReleaseDate": "2020-01-01T00:00:00.000Z",
                                    "viewableDate": "2026-12-01T00:00:00.000Z",
                                    "approximateReleasePlan": None,
                                }
                            ],
                            "paging": {"count": 1, "total": 1},
                        }
                    }
                }
            },
            tag_names_by_id={"1296": "Casual", "1393": "Simulation"},
        )

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game.title, "Example Epic Game")
        self.assertEqual(game.release_date, "2027-06-12")
        self.assertEqual(game.date_accuracy, "exact")
        self.assertEqual(game.launch_time_utc, "2027-06-12T15:00:00Z")
        self.assertEqual(game.source_url, "https://store.epicgames.com/en-US/p/example-epic-game")
        self.assertEqual(game.platform_slugs, ["pc", "epic-games-store"])
        self.assertEqual(game.publishers, ["Example Publisher"])
        self.assertEqual(game.developers, ["Example Studio"])
        self.assertEqual(game.external_ids["epicOfferId"], "offer-1")
        self.assertEqual(game.external_ids["epicNamespace"], "namespace-1")
        self.assertEqual(game.external_ids["epicProductId"], "product-1")

        link = game.store_links[0]
        self.assertEqual(link.store_name, "epic_games_store")
        self.assertEqual(link.id, "epic_games_store:product-1")
        self.assertEqual(link.product_id, "product-1")
        self.assertEqual(link.sku_id, "offer-1")
        self.assertEqual(link.price_text, "$49.99")
        self.assertEqual(link.price, 49.99)
        self.assertEqual(link.currency, "USD")
        self.assertTrue(link.preorder_available)
        self.assertEqual(link.release_date_text, "2027-06-12T15:00:00.000Z")
        self.assertEqual(link.metadata["namespace"], "namespace-1")
        self.assertEqual(link.metadata["categories"], ["games", "games/edition/base"])
        self.assertEqual(link.metadata["tagIds"], ["1296", "1393"])
        self.assertEqual(link.metadata["tags"], ["Casual", "Simulation"])


if __name__ == "__main__":
    unittest.main()
