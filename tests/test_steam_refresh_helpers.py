from __future__ import annotations

import unittest

from steam_metadata_backfill import _row_to_game
from tracked.steam_tracked_refresh import _tracked_steam_rows


class _FakeColumn:
    name = "id"


class _FakeCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params = None
        self.description = [_FakeColumn()]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql, params=None) -> None:
        self.sql = sql
        self.params = params

    def fetchall(self) -> list:
        return []


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


class _FakeStore:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.conn = _FakeConnection(cursor)


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

    def test_tracked_rows_include_recent_released_games_missing_price(self) -> None:
        cursor = _FakeCursor()
        _tracked_steam_rows(_FakeStore(cursor), 80)

        self.assertIn("g.status IN ('upcoming', 'released')", cursor.sql)
        self.assertIn("INTERVAL '14 days'", cursor.sql)
        self.assertIn("sl.price IS NULL", cursor.sql)
        self.assertIn("NULLIF(BTRIM(sl.price_text), '') IS NULL", cursor.sql)


if __name__ == "__main__":
    unittest.main()
