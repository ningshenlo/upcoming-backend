from __future__ import annotations

import unittest

from tracked.playstation_tracked_refresh import _fallback_concept, _platform_names


class PlayStationRefreshHelpersTest(unittest.TestCase):
    def test_fallback_concept_keeps_tracked_identity(self) -> None:
        fallback = _fallback_concept(
            {
                "concept_id": "100000",
                "title": "Example Game",
                "platform_slugs": ["ps5"],
            }
        )

        self.assertEqual(fallback["id"], "100000")
        self.assertEqual(fallback["name"], "Example Game")
        self.assertEqual(fallback["defaultProduct"]["platforms"], ["PS5"])

    def test_platform_names_defaults_to_ps5(self) -> None:
        self.assertEqual(_platform_names(None), ["PS5"])
        self.assertEqual(_platform_names(["ps4", "ps5"]), ["PS4", "PS5"])


if __name__ == "__main__":
    unittest.main()
