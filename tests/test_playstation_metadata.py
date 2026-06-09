from __future__ import annotations

import unittest

from collectors.playstation import _game_from_concept, _store_link_from_product


class PlayStationMetadataTest(unittest.TestCase):
    def test_store_link_metadata_includes_product_context(self) -> None:
        link = _store_link_from_product(
            {
                "id": "UP0000-PPSA00000_00-EXAMPLE",
                "platforms": ["PS5"],
                "npTitleId": "PPSA00000_00",
                "storeDisplayClassification": "FULL_GAME",
                "localizedStoreDisplayClassification": "Full Game",
                "combinedLocalizedGenres": [{"value": "Action"}, {"value": "RPG"}],
                "screenLanguages": [{"value": "English"}, {"value": "Chinese"}],
                "spokenLanguages": [{"value": "Japanese"}],
                "compatibilityNotices": [
                    {"type": "NO_OF_PLAYERS", "value": "1"},
                    {"type": "REMOTE_PLAY_SUPPORTED", "value": "true"},
                ],
                "contentRating": {
                    "authority": "ESRB",
                    "name": "ESRB_TEEN",
                    "description": "ESRB Teen",
                    "descriptors": [{"description": "Violence"}],
                    "interactiveElements": [{"description": "In-Game Purchases"}],
                    "url": "https://example.com/rating.png",
                },
                "edition": {"name": "Standard Edition", "type": "STANDARD", "features": ["Base game"]},
                "webctas": [
                    {
                        "type": "PREORDER",
                        "action": {"param": [{"name": "skuId", "value": "SKU-1"}]},
                        "local": {"priceOrText": "$69.99", "ctaLabel": "cta.preorder", "ctaType": "purchase"},
                    }
                ],
                "releaseDate": "2026-06-12T04:00:00Z",
            },
            cache={},
            translations={"cta.preorder": "Pre-Order"},
            source_url="https://store.playstation.com/en-us/concept/100000",
            wishlist_available=True,
            release_date_text=None,
        )

        assert link is not None
        self.assertEqual(link.metadata["genres"], ["Action", "RPG"])
        self.assertEqual(link.metadata["tags"], ["Action", "RPG"])
        self.assertEqual(link.metadata["languages"], ["English", "Chinese", "Japanese"])
        self.assertEqual(link.metadata["contentRating"]["description"], "ESRB Teen")
        self.assertEqual(link.metadata["contentRating"]["descriptors"], ["Violence"])
        self.assertEqual(link.metadata["compatibilityNotices"][0], {"type": "NO_OF_PLAYERS", "value": "1"})
        self.assertEqual(link.metadata["localizedStoreDisplayClassification"], "Full Game")

    def test_game_from_concept_maps_developer(self) -> None:
        game = _game_from_concept(
            {
                "id": "100000",
                "name": "Example Game",
                "publisherName": "Example Publisher",
                "developerName": "Example Developer",
                "defaultProduct": {
                    "id": "UP0000-PPSA00000_00-EXAMPLE",
                    "platforms": ["PS5"],
                    "type": "GAME",
                    "subType": "FULL_GAME",
                },
            },
            fallback={},
        )

        assert game is not None
        self.assertEqual(game.publishers, ["Example Publisher"])
        self.assertEqual(game.developers, ["Example Developer"])

    def test_store_link_metadata_uses_detail_context_when_product_is_sparse(self) -> None:
        link = _store_link_from_product(
            {
                "id": "UP0000-PPSA00000_00-EXAMPLE",
                "platforms": ["PS5"],
                "edition": {"name": "Standard Edition", "type": "STANDARD"},
                "webctas": [{"local": {"priceOrText": "$59.99"}}],
            },
            cache={},
            translations={},
            source_url="https://store.playstation.com/en-us/concept/100000",
            wishlist_available=None,
            release_date_text=None,
            context_product={
                "combinedLocalizedGenres": [{"value": "Adventure"}],
                "screenLanguages": [{"value": "English"}],
                "compatibilityNotices": [{"type": "OFFLINE_PLAY_MODE", "value": "ENABLED"}],
                "contentRating": {"authority": "ESRB", "description": "ESRB Everyone"},
            },
        )

        assert link is not None
        self.assertEqual(link.metadata["genres"], ["Adventure"])
        self.assertEqual(link.metadata["languages"], ["English"])
        self.assertEqual(link.metadata["contentRating"]["description"], "ESRB Everyone")
        self.assertEqual(link.metadata["compatibilityNotices"], [{"type": "OFFLINE_PLAY_MODE", "value": "ENABLED"}])


if __name__ == "__main__":
    unittest.main()
