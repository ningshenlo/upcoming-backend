from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    youtube_api_key: str | None
    raw_archive_dir: Path
    r2_bucket_name: str | None
    r2_endpoint_url: str | None
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    require_r2_archive: bool
    user_agent: str
    dataforseo_api_key: str | None = None
    dataforseo_pingback_url: str | None = None
    dataforseo_youtube_location_code: int = 2840
    dataforseo_youtube_language_code: str = "en"
    dataforseo_youtube_block_depth: int = 10
    r2_use_wrangler: bool = False

    @property
    def has_r2_credentials(self) -> bool:
        return all(
            [
                self.r2_bucket_name,
                self.r2_endpoint_url,
                self.r2_access_key_id,
                self.r2_secret_access_key,
            ]
        )

    @property
    def has_r2_wrangler(self) -> bool:
        return bool(self.r2_bucket_name and self.r2_use_wrangler)

    @property
    def has_r2(self) -> bool:
        return self.has_r2_credentials or self.has_r2_wrangler


def load_settings() -> Settings:
    require_r2_archive = os.environ.get("REQUIRE_R2_ARCHIVE", "").lower() in {"1", "true", "yes"}
    r2_use_wrangler = os.environ.get("R2_WRANGLER_UPLOAD", "").lower() in {"1", "true", "yes"}
    return Settings(
        database_url=os.environ.get("DATABASE_URL"),
        youtube_api_key=os.environ.get("YOUTUBE_API_KEY"),
        raw_archive_dir=Path(os.environ.get("RAW_ARCHIVE_DIR", "var/raw")),
        r2_bucket_name=os.environ.get("R2_BUCKET_NAME"),
        r2_endpoint_url=_r2_endpoint_url(),
        r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        require_r2_archive=require_r2_archive,
        user_agent=os.environ.get(
            "UPCOMING_GAMES_USER_AGENT",
            "UpcomingGamesBot/0.1 (+https://upcominggames.com)",
        ),
        dataforseo_api_key=os.environ.get("DFS") or os.environ.get("DATAFORSEO_API_KEY"),
        dataforseo_pingback_url=os.environ.get("DATAFORSEO_PINGBACK_URL"),
        dataforseo_youtube_location_code=_int_env("DATAFORSEO_YOUTUBE_LOCATION_CODE", 2840),
        dataforseo_youtube_language_code=os.environ.get("DATAFORSEO_YOUTUBE_LANGUAGE_CODE", "en"),
        dataforseo_youtube_block_depth=_int_env("DATAFORSEO_YOUTUBE_BLOCK_DEPTH", 10),
        r2_use_wrangler=r2_use_wrangler,
    )


def _r2_endpoint_url() -> str | None:
    endpoint_url = os.environ.get("R2_ENDPOINT_URL")
    if endpoint_url:
        return endpoint_url
    account_id = os.environ.get("R2_ACCOUNT_ID")
    if account_id:
        return f"https://{account_id}.r2.cloudflarestorage.com"
    return None


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default
