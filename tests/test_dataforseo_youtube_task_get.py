from __future__ import annotations

import unittest

from dataforseo_youtube_task_get import (
    DFS_YOUTUBE_TASK_SOURCE,
    DFSTASK_PENDING,
    DFSTASK_PROCESSING,
    DFSTASK_SUBMITTED,
    _claim_pending_tasks,
    parse_dataforseo_youtube_candidates,
)


class DataForSeoYouTubeTaskGetTest(unittest.TestCase):
    def test_claim_pending_tasks_includes_submitted_tasks(self) -> None:
        store = _FakeStore()

        tasks = _claim_pending_tasks(store, 5)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, "task-1")
        self.assertEqual(
            store.conn.cursor_instance.params,
            (
                DFSTASK_PENDING,
                DFSTASK_SUBMITTED,
                DFS_YOUTUBE_TASK_SOURCE,
                f"{DFS_YOUTUBE_TASK_SOURCE}:%",
                5,
                DFSTASK_PROCESSING,
            ),
        )

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


class _FakeStore:
    def __init__(self) -> None:
        self.conn = _FakeConn()


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def cursor(self):
        return self.cursor_instance


class _FakeCursor:
    def __init__(self) -> None:
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql, params) -> None:
        self.params = params

    def fetchall(self):
        return [("task-1", f"{DFS_YOUTUBE_TASK_SOURCE}:game_id=game-1")]


if __name__ == "__main__":
    unittest.main()
