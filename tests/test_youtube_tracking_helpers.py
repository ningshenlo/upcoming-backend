from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import Settings
from hot_tracker import parse_channel_names, run_hot_tracker
from hot_trackers.youtube import (
    GameRow,
    YouTubeCandidate,
    YouTubeRunResult,
    build_discovery_query,
    _dataforseo_pingback_url,
    dataforseo_youtube_task_source_for_game,
    extract_youtube_video_id,
    score_candidate,
    select_best_candidate,
    run as run_youtube,
)


class YouTubeTrackingHelpersTest(unittest.TestCase):
    def test_extract_youtube_video_id_supports_common_formats(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=abc_123-XY0&t=20s"),
            "abc_123-XY0",
        )
        self.assertEqual(extract_youtube_video_id("https://youtu.be/abc_123-XY0"), "abc_123-XY0")
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/shorts/abc_123-XY0"),
            "abc_123-XY0",
        )

    def test_build_discovery_query_includes_game_context(self) -> None:
        self.assertEqual(build_discovery_query("Brazzante"), "Brazzante game official trailer")

    def test_score_candidate_prefers_official_trailer_over_reaction(self) -> None:
        game = GameRow(
            id="game-1",
            title="Metroid Prime 4",
            aliases=[],
            trailer_url=None,
            publishers=["Nintendo"],
            developers=[],
        )
        official = YouTubeCandidate(
            video_id="official",
            title="Metroid Prime 4: Beyond - Official Trailer",
            description="",
            channel_id="channel-1",
            channel_title="Nintendo of America",
            published_at=None,
            thumbnail_url=None,
            video_url="https://www.youtube.com/watch?v=official",
            match_confidence=0,
            match_reasons=[],
        )
        reaction = YouTubeCandidate(
            video_id="reaction",
            title="Metroid Prime 4 Trailer Reaction",
            description="",
            channel_id="channel-2",
            channel_title="Fan Channel",
            published_at=None,
            thumbnail_url=None,
            video_url="https://www.youtube.com/watch?v=reaction",
            match_confidence=0,
            match_reasons=[],
        )

        self.assertGreater(
            score_candidate(game, official).match_confidence,
            score_candidate(game, reaction).match_confidence,
        )

    def test_select_best_candidate_still_returns_best_non_official_match(self) -> None:
        game = GameRow(
            id="game-1",
            title="Example Game",
            aliases=[],
            trailer_url=None,
            publishers=[],
            developers=[],
        )
        candidates = [
            YouTubeCandidate(
                video_id="review",
                title="Example Game review",
                description="",
                channel_id=None,
                channel_title="Reviewer",
                published_at=None,
                thumbnail_url=None,
                video_url="https://www.youtube.com/watch?v=review",
                match_confidence=0,
                match_reasons=[],
            ),
            YouTubeCandidate(
                video_id="trailer",
                title="Example Game Reveal Trailer",
                description="",
                channel_id=None,
                channel_title="Uploads",
                published_at=None,
                thumbnail_url=None,
                video_url="https://www.youtube.com/watch?v=trailer",
                match_confidence=0,
                match_reasons=[],
            ),
        ]

        matched = select_best_candidate(game, candidates)

        self.assertIsNotNone(matched)
        self.assertEqual(matched.video_id, "trailer")

    def test_select_best_candidate_rejects_unrelated_movie_trailer(self) -> None:
        game = GameRow(
            id="game-1",
            title="Brazzante",
            aliases=[],
            trailer_url=None,
            publishers=[],
            developers=[],
        )
        candidates = [
            YouTubeCandidate(
                video_id="movie",
                title="The Brutalist | Official Trailer HD | A24",
                description="",
                channel_id="channel-1",
                channel_title="A24",
                published_at=None,
                thumbnail_url=None,
                video_url="https://www.youtube.com/watch?v=movie",
                match_confidence=0,
                match_reasons=[],
            )
        ]

        self.assertIsNone(select_best_candidate(game, candidates))

    def test_dataforseo_pingback_url_appends_required_placeholders(self) -> None:
        self.assertEqual(
            _dataforseo_pingback_url("https://example.com/api/pingback"),
            "https://example.com/api/pingback?id=$id&tag=$tag",
        )
        self.assertEqual(
            _dataforseo_pingback_url("https://example.com/api/pingback?source=youtube"),
            "https://example.com/api/pingback?source=youtube&id=$id&tag=$tag",
        )

    def test_dataforseo_task_source_tracks_game_id(self) -> None:
        self.assertEqual(
            dataforseo_youtube_task_source_for_game("game-1"),
            "serp/youtube/organic/task_post:game_id=game-1",
        )


class HotTrackerCliTest(unittest.TestCase):
    def test_parse_channel_names_dedupes_and_defaults(self) -> None:
        self.assertEqual(parse_channel_names("youtube, youtube"), ["youtube"])
        self.assertEqual(parse_channel_names(""), ["youtube"])

    def test_run_hot_tracker_rejects_unknown_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported hot tracker channel"):
            run_hot_tracker(["reddit"], discover_limit=1, refresh_limit=1, rediscovery_days=7)

    def test_run_hot_tracker_dispatches_youtube_channel(self) -> None:
        with patch("hot_tracker.youtube.run") as run_channel:
            run_channel.return_value = YouTubeRunResult(status="success", processed_count=2, failed_count=0)

            result = run_hot_tracker(["youtube"], discover_limit=1, refresh_limit=1, rediscovery_days=7)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.processed_count, 2)
        run_channel.assert_called_once()

    def test_youtube_channel_requires_api_key(self) -> None:
        settings = Settings(
            database_url="postgresql://example",
            youtube_api_key=None,
            raw_archive_dir=Path("var/raw"),
            r2_bucket_name=None,
            r2_endpoint_url=None,
            r2_access_key_id=None,
            r2_secret_access_key=None,
            require_r2_archive=False,
            user_agent="test",
        )

        with self.assertRaisesRegex(RuntimeError, "YOUTUBE_API_KEY"):
            run_youtube(settings, discover_limit=1, refresh_limit=1, rediscovery_days=7)

    def test_youtube_discovery_requires_dataforseo_key(self) -> None:
        settings = Settings(
            database_url="postgresql://example",
            youtube_api_key="youtube-key",
            raw_archive_dir=Path("var/raw"),
            r2_bucket_name=None,
            r2_endpoint_url=None,
            r2_access_key_id=None,
            r2_secret_access_key=None,
            require_r2_archive=False,
            user_agent="test",
        )

        with self.assertRaisesRegex(RuntimeError, "DATAFORSEO_API_KEY"):
            run_youtube(settings, discover_limit=1, refresh_limit=0, rediscovery_days=7)


if __name__ == "__main__":
    unittest.main()
