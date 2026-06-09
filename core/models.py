from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StoreLink:
    store_name: str
    url: str
    platform_slugs: list[str] = field(default_factory=list)
    id: str | None = None
    product_id: str | None = None
    sku_id: str | None = None
    np_title_id: str | None = None
    edition_name: str | None = None
    edition_type: str | None = None
    edition_features: list[str] = field(default_factory=list)
    price_text: str | None = None
    price: float | None = None
    currency: str | None = None
    preorder_available: bool | None = None
    wishlist_available: bool | None = None
    demo_available: bool | None = None
    release_date_text: str | None = None
    affiliate_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectedEvent:
    event_type: str
    title: str | None = None
    date: str | None = None
    date_accuracy: str = "unknown"
    launch_time_utc: str | None = None
    platform_slugs: list[str] = field(default_factory=list)
    region: str = "global"
    status: str = "confirmed"
    confidence: int = 80
    source_url: str | None = None


@dataclass(frozen=True)
class CollectedGame:
    title: str
    source_slug: str
    source_url: str
    platform_slugs: list[str]
    release_date: str | None = None
    date_accuracy: str = "unknown"
    launch_time_utc: str | None = None
    description: str | None = None
    short_description: str | None = None
    cover_image_url: str | None = None
    header_image_url: str | None = None
    screenshot_urls: list[str] = field(default_factory=list)
    trailer_url: str | None = None
    trailer_thumbnail_url: str | None = None
    publishers: list[str] = field(default_factory=list)
    developers: list[str] = field(default_factory=list)
    store_links: list[StoreLink] = field(default_factory=list)
    events: list[CollectedEvent] = field(default_factory=list)
    external_ids: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectorResult:
    source_slug: str
    fetched_url: str
    raw_payload: Any
    games: list[CollectedGame]
