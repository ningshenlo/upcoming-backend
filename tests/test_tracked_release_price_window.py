from __future__ import annotations

import unittest

from tracked.epic_tracked_refresh import _tracked_epic_rows
from tracked.gog_tracked_refresh import _tracked_gog_rows
from tracked.nintendo_tracked_refresh import _tracked_nintendo_rows
from tracked.playstation_tracked_refresh import _tracked_playstation_rows
from tracked.xbox_tracked_refresh import _tracked_xbox_rows


class _FakeColumn:
    name = "id"


class _FakeCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.description = [_FakeColumn()]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql, params=None) -> None:
        self.sql = sql

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


class TrackedReleasePriceWindowTest(unittest.TestCase):
    def test_other_platforms_include_recent_released_games_missing_price(self) -> None:
        for row_query in (
            _tracked_epic_rows,
            _tracked_gog_rows,
            _tracked_nintendo_rows,
            _tracked_playstation_rows,
            _tracked_xbox_rows,
        ):
            with self.subTest(row_query=row_query.__name__):
                cursor = _FakeCursor()
                row_query(_FakeStore(cursor), 80)

                self.assertIn("g.status IN ('upcoming', 'released')", cursor.sql)
                self.assertIn("INTERVAL '14 days'", cursor.sql)
                self.assertIn("sl.price IS NULL", cursor.sql)
                self.assertIn("NULLIF(BTRIM(sl.price_text), '') IS NULL", cursor.sql)


if __name__ == "__main__":
    unittest.main()
