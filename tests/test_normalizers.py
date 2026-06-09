from __future__ import annotations

import unittest

from core.normalizers import parse_release_date


class ParseReleaseDateTest(unittest.TestCase):
    def test_exact_date(self) -> None:
        self.assertEqual(parse_release_date("June 12, 2026"), ("2026-06-12", "exact"))

    def test_month_accuracy(self) -> None:
        self.assertEqual(parse_release_date("June 2026"), ("2026-06-01", "month"))

    def test_quarter_accuracy(self) -> None:
        self.assertEqual(parse_release_date("Q3 2026"), ("2026-07-01", "quarter"))

    def test_year_accuracy(self) -> None:
        self.assertEqual(parse_release_date("2026"), ("2026-01-01", "year"))


if __name__ == "__main__":
    unittest.main()
