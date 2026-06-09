from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def strip_tags(value: str) -> str:
    return SPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", value))).strip()


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "untitled"


def parse_release_date(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "unknown"

    cleaned = SPACE_RE.sub(" ", strip_tags(value).replace(",", " ")).strip()
    if not cleaned or cleaned.lower() in {"coming soon", "tba", "to be announced"}:
        return None, "unknown"

    for fmt in ("%b %d %Y", "%B %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
            return dt.date().isoformat(), "exact"
        except ValueError:
            pass

    try:
        dt = parsedate_to_datetime(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.date().isoformat(), "exact"
    except (TypeError, ValueError, IndexError):
        pass

    month_match = re.fullmatch(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})",
        cleaned,
        flags=re.IGNORECASE,
    )
    if month_match:
        month = _month_number(month_match.group(1))
        return f"{month_match.group(2)}-{month:02d}-01", "month"

    quarter_match = re.fullmatch(r"q([1-4])\s+(\d{4})", cleaned, flags=re.IGNORECASE)
    if quarter_match:
        month = (int(quarter_match.group(1)) - 1) * 3 + 1
        return f"{quarter_match.group(2)}-{month:02d}-01", "quarter"

    if re.fullmatch(r"\d{4}", cleaned):
        return f"{cleaned}-01-01", "year"

    return None, "unknown"


def _month_number(value: str) -> int:
    return {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }[value[:3].lower()]
