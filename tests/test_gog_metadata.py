from __future__ import annotations

import unittest

from collectors.gog import parse_catalog_payload, parse_game_payload


class GogMetadataTest(unittest.TestCase):
    def test_catalog_payload_maps_to_game_and_store_link(self) -> None:
        games = parse_catalog_payload(
            {
                "products": [
                    {
                        "id": "1234567890",
                        "slug": "example_gog_game",
                        "title": "Example GOG Game",
                        "productType": "game",
                        "productState": "coming-soon",
                        "storeLink": "https://www.gog.com/en/game/example_gog_game",
                        "releaseDate": "2099.06.12",
                        "storeReleaseDate": "2099.06.12",
                        "price": {
                            "final": "$12.99",
                            "finalMoney": {"amount": "12.99", "currency": "USD"},
                        },
                        "genres": [{"name": "Adventure", "slug": "adventure"}],
                        "tags": [{"name": "Indie", "slug": "indie"}],
                        "features": [{"name": "Single-player", "slug": "single"}],
                        "operatingSystems": ["windows", "linux"],
                        "developers": ["Example Studio"],
                        "publishers": ["Example Publisher"],
                        "coverVertical": "https://example.com/cover.jpg",
                        "coverHorizontal": "https://example.com/header.png",
                        "screenshots": ["https://example.com/screen_{formatter}.jpg"],
                    }
                ]
            }
        )

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game.title, "Example GOG Game")
        self.assertEqual(game.source_slug, "gog")
        self.assertEqual(game.source_url, "https://www.gog.com/en/game/example_gog_game")
        self.assertEqual(game.platform_slugs, ["pc", "gog"])
        self.assertEqual(game.release_date, "2099-06-12")
        self.assertEqual(game.date_accuracy, "exact")
        self.assertEqual(game.screenshot_urls, ["https://example.com/screen_1600.jpg"])
        self.assertEqual(game.external_ids["gogProductId"], "1234567890")
        self.assertEqual(game.external_ids["gogSlug"], "example_gog_game")

        link = game.store_links[0]
        self.assertEqual(link.id, "gog:1234567890")
        self.assertEqual(link.store_name, "gog")
        self.assertEqual(link.product_id, "1234567890")
        self.assertEqual(link.sku_id, "example_gog_game")
        self.assertEqual(link.price_text, "$12.99")
        self.assertEqual(link.price, 12.99)
        self.assertEqual(link.currency, "USD")
        self.assertTrue(link.preorder_available)
        self.assertEqual(link.metadata["genres"], ["Adventure"])
        self.assertEqual(link.metadata["tags"], ["Indie"])
        self.assertEqual(link.metadata["operatingSystems"], ["windows", "linux"])

    def test_catalog_payload_keeps_historical_release_date_in_metadata_only(self) -> None:
        games = parse_catalog_payload(
            {
                "products": [
                    {
                        "id": "1234567890",
                        "slug": "example_gog_game",
                        "title": "Example GOG Game",
                        "productType": "game",
                        "storeLink": "https://www.gog.com/en/game/example_gog_game",
                        "releaseDate": "2020.09.03",
                    }
                ]
            }
        )

        self.assertEqual(games[0].release_date, None)
        self.assertEqual(games[0].date_accuracy, "unknown")
        self.assertEqual(games[0].store_links[0].metadata["releaseDate"], "2020.09.03")

    def test_game_payload_maps_detail_fields(self) -> None:
        games = parse_game_payload(
            {
                "releaseStatus": "coming-soon",
                "overview": "<p>Detailed copy.</p>",
                "_links": {
                    "store": {"href": "https://www.gog.com/en/game/example_gog_game"},
                    "boxArtImage": {"href": "https://example.com/box.jpg"},
                    "galaxyBackgroundImage": {"href": "https://example.com/hero.jpg"},
                },
                "_embedded": {
                    "product": {
                        "id": 1234567890,
                        "title": "Example GOG Game",
                        "globalReleaseDate": "2099-06-12T00:00:00+00:00",
                        "gogReleaseDate": "2099-06-12T00:00:00+00:00",
                        "category": "GAME",
                        "isPreorder": True,
                    },
                    "publisher": {"name": "Example Publisher"},
                    "developers": [{"name": "Example Studio"}],
                    "tags": [{"name": "Adventure"}],
                    "features": [{"name": "Single-player"}],
                    "supportedOperatingSystems": [
                        {"operatingSystem": {"name": "windows"}},
                    ],
                    "screenshots": [
                        {"_links": {"self": {"href": "https://example.com/screen_{formatter}.jpg"}}},
                    ],
                    "videos": [
                        {
                            "_links": {
                                "self": {"href": "https://www.youtube.com/embed/example"},
                                "thumbnail": {"href": "https://example.com/thumb.jpg"},
                            }
                        }
                    ],
                },
            }
        )

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game.release_date, "2099-06-12")
        self.assertEqual(game.launch_time_utc, "2099-06-12T00:00:00Z")
        self.assertEqual(game.description, "Detailed copy.")
        self.assertEqual(game.publishers, ["Example Publisher"])
        self.assertEqual(game.developers, ["Example Studio"])
        self.assertEqual(game.screenshot_urls, ["https://example.com/screen_1600.jpg"])
        self.assertEqual(game.trailer_url, "https://www.youtube.com/embed/example")
        self.assertTrue(game.store_links[0].preorder_available)

    def test_game_payload_can_parse_released_price_for_tracked_refresh(self) -> None:
        payload = {
            "releaseStatus": "released",
            "_embedded": {
                "product": {
                    "id": 1234567890,
                    "title": "Example GOG Game",
                    "globalReleaseDate": "2026-06-12T00:00:00+00:00",
                    "category": "GAME",
                    "price": {
                        "final": "$12.99",
                        "finalMoney": {"amount": "12.99", "currency": "USD"},
                    },
                },
            },
        }

        self.assertEqual(parse_game_payload(payload), [])
        games = parse_game_payload(payload, allow_released=True)

        self.assertEqual(len(games), 1)
        link = games[0].store_links[0]
        self.assertEqual(link.price_text, "$12.99")
        self.assertEqual(link.price, 12.99)
        self.assertEqual(link.currency, "USD")


if __name__ == "__main__":
    unittest.main()
