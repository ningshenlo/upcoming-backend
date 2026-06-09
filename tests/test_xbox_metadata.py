from __future__ import annotations

import unittest

from collectors.xbox import parse_preloaded_state, parse_products_payload


class XboxMetadataTest(unittest.TestCase):
    def test_preloaded_state_parser_reads_first_json_object(self) -> None:
        state = parse_preloaded_state(
            """
            <script>
              window.__PRELOADED_STATE__ = {"appContext":{"telemetryInfo":{"initialCv":"abc.0"}}};
              window.env = {"ignored":true};
            </script>
            """
        )

        self.assertEqual(state["appContext"]["telemetryInfo"]["initialCv"], "abc.0")

    def test_product_payload_maps_to_game_and_store_link(self) -> None:
        games = parse_products_payload(
            {
                "productSummaries": [
                    {
                        "productId": "9TEST123456",
                        "title": "Example Xbox Game",
                        "releaseDate": "2027-06-12T00:00:00.0000000Z",
                        "availableOn": ["PC", "XboxOne", "XboxSeriesX", "Handheld"],
                        "categories": ["Action & adventure", "Role playing"],
                        "description": "A test game.",
                        "shortDescription": "Test short copy.",
                        "developerName": "Example Studio",
                        "publisherName": "Example Publisher",
                        "productKind": "Game",
                        "productFamily": "Games",
                        "preferredSkuId": "0010",
                        "images": {
                            "boxArt": {"url": "https://example.com/box.jpg"},
                            "superHeroArt": {"url": "https://example.com/hero.jpg"},
                        },
                        "contentRating": {
                            "boardName": "ESRB",
                            "rating": "TEEN",
                            "ratingAge": 13,
                            "descriptors": ["Violence"],
                        },
                    }
                ],
                "skuSummaries": [
                    {
                        "productId": "9TEST123456",
                        "skuId": "0017",
                        "skuTitle": "Example Xbox Game",
                        "isPreorder": True,
                    }
                ],
                "availabilitySummaries": [
                    {
                        "productId": "9TEST123456",
                        "skuId": "0017",
                        "availabilityId": "AVAIL1",
                        "actions": ["Purchase", "Browse"],
                        "price": {
                            "listPrice": 59.99,
                            "msrp": 69.99,
                            "currency": "USD",
                        },
                    }
                ],
            }
        )

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game.title, "Example Xbox Game")
        self.assertEqual(game.release_date, "2027-06-12")
        self.assertEqual(game.date_accuracy, "exact")
        self.assertIsNone(game.launch_time_utc)
        self.assertEqual(game.platform_slugs, ["pc", "xbox-one", "xbox-series"])
        self.assertEqual(game.publishers, ["Example Publisher"])
        self.assertEqual(game.developers, ["Example Studio"])
        self.assertEqual(game.external_ids["xboxProductId"], "9TEST123456")

        link = game.store_links[0]
        self.assertEqual(link.store_name, "xbox_store")
        self.assertEqual(link.product_id, "9TEST123456")
        self.assertEqual(link.sku_id, "0017")
        self.assertEqual(link.price_text, "$59.99")
        self.assertEqual(link.price, 59.99)
        self.assertEqual(link.currency, "USD")
        self.assertTrue(link.preorder_available)
        self.assertEqual(link.metadata["categories"], ["Action & adventure", "Role playing"])
        self.assertEqual(link.metadata["contentRating"]["rating"], "TEEN")


if __name__ == "__main__":
    unittest.main()
