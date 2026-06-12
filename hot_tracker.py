from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from core.config import load_settings
from hot_trackers import youtube

DEFAULT_CHANNELS = "youtube"
SUPPORTED_CHANNELS = {"youtube"}


@dataclass(frozen=True)
class HotTrackerResult:
    status: str
    processed_count: int
    failed_count: int


def run_hot_tracker(
    channels: list[str],
    discover_limit: int,
    refresh_limit: int,
    rediscovery_days: int,
) -> HotTrackerResult:
    unknown_channels = [channel for channel in channels if channel not in SUPPORTED_CHANNELS]
    if unknown_channels:
        raise ValueError(f"Unsupported hot tracker channel(s): {', '.join(unknown_channels)}")

    settings = load_settings()
    results: list[HotTrackerResult] = []
    for channel in channels:
        if channel == "youtube":
            result = youtube.run(
                settings,
                discover_limit=discover_limit,
                refresh_limit=refresh_limit,
                rediscovery_days=rediscovery_days,
            )
            results.append(
                HotTrackerResult(
                    status=result.status,
                    processed_count=result.processed_count,
                    failed_count=result.failed_count,
                )
            )

    failed_count = sum(result.failed_count for result in results)
    processed_count = sum(result.processed_count for result in results)
    if any(result.status == "failed" for result in results):
        status = "failed"
    elif any(result.status == "partial_success" for result in results):
        status = "partial_success"
    else:
        status = "success"
    return HotTrackerResult(status=status, processed_count=processed_count, failed_count=failed_count)


def parse_channel_names(value: str) -> list[str]:
    channels = [item.strip().lower() for item in value.split(",") if item.strip()]
    return list(dict.fromkeys(channels)) or [DEFAULT_CHANNELS]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run game hot tracking channels.")
    parser.add_argument("--channels", default=os.environ.get("HOT_TRACKER_CHANNELS", DEFAULT_CHANNELS))
    parser.add_argument("--discover-limit", type=int, default=_int_env("HOT_TRACKER_DISCOVERY_LIMIT", 2000))
    parser.add_argument("--refresh-limit", type=int, default=_int_env("HOT_TRACKER_REFRESH_LIMIT", 5000))
    parser.add_argument("--rediscovery-days", type=int, default=_int_env("HOT_TRACKER_REDISCOVERY_DAYS", 7))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_hot_tracker(
            channels=parse_channel_names(args.channels),
            discover_limit=args.discover_limit,
            refresh_limit=args.refresh_limit,
            rediscovery_days=args.rediscovery_days,
        )
    except ValueError as exc:
        print(f"hot-tracker failed: {exc}", file=sys.stderr)
        return 2
    print(
        "hot-tracker completed: "
        f"status={result.status} processed={result.processed_count} failed={result.failed_count}"
    )
    return 1 if result.status == "failed" else 0


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
