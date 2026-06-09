from __future__ import annotations

import unittest

from steam_metadata_backfill import _row_to_game


class SteamRefreshHelpersTest(unittest.TestCase):
    def test_row_to_game_keeps_missing_release_date_empty(self) -> None:
        game = _row_to_game(
            {
                "title": "Example Game",
                "official_url": None,
                "cover_image_url": None,
                "steam_app_id": 123,
                "release_date": None,
                "date_accuracy": "unknown",
            }
        )

        self.assertIsNone(game.release_date)
        self.assertEqual(game.source_url, "https://store.steampowered.com/app/123/")


if __name__ == "__main__":
    unittest.main()
