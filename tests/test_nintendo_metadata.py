from __future__ import annotations

import unittest

from collectors.nintendo import parse_algolia_payload


class NintendoMetadataTest(unittest.TestCase):
    def test_algolia_hit_maps_to_game_and_store_link(self) -> None:
        games = parse_algolia_payload(
            {
                "hits": [
                    {
                        "title": "Example Game",
                        "url": "/us/store/products/example-game-switch-2/",
                        "urlKey": "example-game-switch-2",
                        "sku": "7100000000",
                        "nsuid": "70010000000000",
                        "availability": ["Pre-order", "Coming soon"],
                        "releaseDate": "2027-12-31T00:00:00.000Z",
                        "releaseDateDisplay": "2027",
                        "platform": "Nintendo Switch 2",
                        "platformCode": "NINTENDO_SWITCH_2",
                        "description": "A test game.",
                        "price": {"finalPrice": 49.99},
                        "contentRating": {"system": "ESRB", "code": "e10", "label": "Everyone 10+"},
                        "gameGenreLabels": ["Action"],
                        "gameFeatureLabels": ["Online Play"],
                        "topLevelFilters": ["Demo available"],
                        "demoNsuid": "70010000000001",
                        "productImage": "store/software/switch2/70010000000000/keyart",
                        "productGallery": [{"publicId": "store/software/switch2/70010000000000/screen", "resourceType": "image"}],
                        "softwarePublisher": "Nintendo",
                        "softwareDeveloper": "Example Studio",
                    }
                ]
            }
        )

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game.title, "Example Game")
        self.assertEqual(game.release_date, "2027-01-01")
        self.assertEqual(game.date_accuracy, "year")
        self.assertIsNone(game.launch_time_utc)
        self.assertEqual(game.platform_slugs, ["nintendo-switch-2"])
        self.assertEqual(game.publishers, ["Nintendo"])
        self.assertEqual(game.developers, ["Example Studio"])
        self.assertEqual(game.external_ids["nintendoNsuid"], "70010000000000")

        link = game.store_links[0]
        self.assertEqual(link.store_name, "nintendo_eshop")
        self.assertEqual(link.product_id, "70010000000000")
        self.assertEqual(link.sku_id, "7100000000")
        self.assertEqual(link.price, 49.99)
        self.assertEqual(link.currency, "USD")
        self.assertTrue(link.preorder_available)
        self.assertTrue(link.demo_available)
        self.assertEqual(link.metadata["genres"], ["Action"])
        self.assertEqual(link.metadata["contentRating"]["label"], "Everyone 10+")
        self.assertEqual(game.events[0].event_type, "demo")


if __name__ == "__main__":
    unittest.main()
