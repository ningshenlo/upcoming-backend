from __future__ import annotations

import unittest

from dataforseo_youtube_task_get import parse_dataforseo_youtube_candidates


class DataForSeoYouTubeTaskGetTest(unittest.TestCase):
    def test_parse_dataforseo_youtube_candidates_filters_video_items(self) -> None:
        payload = {
            "status_code": 20000,
            "tasks": [
                {
                    "status_code": 20000,
                    "result": [
                        {
                            "keyword": "copa city",
                            "items": [
                                {
                                    "type": "youtube_video",
                                    "title": "Copa City - reveal cinematic trailer",
                                    "url": "https://www.youtube.com/watch?v=zbw331U5pfs",
                                    "video_id": "zbw331U5pfs",
                                    "thumbnail_url": "https://i.ytimg.com/vi/zbw331U5pfs/hq720.jpg",
                                    "channel_id": "UC-lAF2H2nVccDInhlDsZRKw",
                                    "channel_name": "Copa City",
                                    "description": "The cinematic trailer for Copa City has arrived.",
                                    "is_live": False,
                                    "is_shorts": False,
                                    "timestamp": "2024-06-12 07:42:57 +00:00",
                                },
                                {
                                    "type": "youtube_video",
                                    "title": "Copa City Short",
                                    "video_id": "short123",
                                    "is_shorts": True,
                                },
                                {
                                    "type": "youtube_channel",
                                    "title": "Copa City",
                                },
                            ],
                        }
                    ],
                }
            ],
        }

        candidates = parse_dataforseo_youtube_candidates(payload)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].video_id, "zbw331U5pfs")
        self.assertEqual(candidates[0].channel_title, "Copa City")
        self.assertEqual(candidates[0].published_at, "2024-06-12 07:42:57 +00:00")


if __name__ == "__main__":
    unittest.main()
