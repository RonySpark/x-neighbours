#!/usr/bin/env python3
"""Saved liker, reposter, and quote files, checked through the report."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-11-16T12:00:00Z"


def dump(folder, name, doc):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def user(uid, username):
    return {"id": uid, "name": username.title(), "username": username}


def post(pid, created, likes=0, reposts=0, quotes=0, text="hello", **extra):
    row = {
        "id": pid,
        "author_id": "1",
        "created_at": created,
        "text": text,
        "conversation_id": pid,
        "public_metrics": {
            "like_count": likes,
            "reply_count": 0,
            "repost_count": reposts,
            "quote_count": quotes,
        },
    }
    row.update(extra)
    return row


def likes(folder, pid, people):
    dump(folder, f"likes_{pid}.json", {
        "data": [user(uid, name) for uid, name in people],
    })


def run_report(posts, windows, prepare=None):
    tmp = tempfile.mkdtemp(prefix="xn-saved-")
    data = os.path.join(tmp, "data")
    out = os.path.join(tmp, "out")
    os.makedirs(data)
    os.makedirs(out)
    dump(data, "posts_1.json", {
        "data": posts,
        "includes": {"users": [user("1", "owner")]},
    })
    if prepare:
        prepare(data)
    cmd = [
        sys.executable, os.path.join(ROOT, "x_neighbours.py"),
        "--config", os.path.join(ROOT, "config.example.json"),
        "--handle", "owner",
        "--user-id", "1",
        "--data", data,
        "--out", out,
        "--now", NOW,
        "--windows", windows,
        "--no-picture",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    payload = json.loads(result.stdout)[windows]
    with open(payload["report_md"], encoding="utf-8") as fh:
        report = fh.read()
    return report, payload


class SavedReadNotesTest(unittest.TestCase):
    def test_week_notes_follow_the_shared_budget(self):
        posts = [
            post("10", "2026-11-16T08:00:00Z", likes=2),
            post("11", "2026-11-13T12:00:00Z", likes=2),
            post("12", "2026-11-13T12:00:00Z", likes=4),
            post("13", "2026-11-16T08:00:00Z", reposts=1),
            post("14", "2026-11-13T12:00:00Z", reposts=2),
            post("15", "2026-11-16T08:00:00Z", likes=1),
        ]

        def prepare(folder):
            likes(folder, "15", [("101", "kept")])

        report, payload = run_report(posts, "week", prepare)
        self.assertIn("likers not fetched for 2 posts with likes", report)
        self.assertIn("reposters not fetched for 1 posts with reposts", report)
        self.assertIn("likers not fetched for 2 posts with likes", payload["notes"])
        self.assertNotIn("post 11", report)

    def test_day_window_ignores_posts_older_than_24_hours(self):
        posts = [
            post("10", "2026-11-16T08:00:00Z", likes=2),
            post("12", "2026-11-13T12:00:00Z", likes=4),
        ]
        report, payload = run_report(posts, "day")
        self.assertIn("likers not fetched for 1 posts with likes", report)
        self.assertEqual(payload["notes"], ["likers not fetched for 1 posts with likes"])

    def test_short_liker_file_is_noted_only_when_clearly_truncated(self):
        posts = [
            post("20", "2026-11-16T08:00:00Z", likes=10),
            post("21", "2026-11-16T08:00:00Z", likes=10),
            post("22", "2026-11-16T08:00:00Z", likes=9),
        ]

        def prepare(folder):
            likes(folder, "20", [(str(200 + i), f"u{i}") for i in range(7)])
            likes(folder, "21", [(str(300 + i), f"v{i}") for i in range(8)])
            likes(folder, "22", [("400", "only")])

        report, _payload = run_report(posts, "day", prepare)
        self.assertIn("likers look truncated for post 20 (saved 7 of 10)", report)
        self.assertNotIn("post 21", report)
        self.assertNotIn("post 22", report)

    def test_missing_quote_file_follows_the_shared_budget(self):
        posts = [
            post("40", "2026-11-16T08:00:00Z", quotes=2),
            post("41", "2026-11-13T12:00:00Z", quotes=2),
            post("42", "2026-11-13T12:00:00Z", quotes=3),
            post("43", "2026-11-16T08:00:00Z", quotes=1),
            post("45", "2026-11-16T08:00:00Z", quotes=1),
            post(
                "44", "2026-11-16T08:00:00Z", quotes=4, text="RT @other: borrowed",
                referenced_tweets=[{"type": "retweeted", "id": "9"}],
            ),
        ]

        def prepare(folder):
            dump(folder, "quotes_43.json", {"data": [{
                "id": "430",
                "author_id": "430",
                "created_at": "2026-11-16T09:00:00Z",
                "text": "quoting",
                "conversation_id": "430",
            }], "includes": {"users": [user("430", "quill")]}})
            dump(folder, "quotes_45.json", {"data": []})

        report, payload = run_report(posts, "week", prepare)
        self.assertIn("quoters not fetched for 2 posts with quotes", report)
        self.assertIn("quoters not fetched for 2 posts with quotes", payload["notes"])
        self.assertNotIn("post 41", report)

        day_report, day_payload = run_report(posts, "day", prepare)
        self.assertIn("quoters not fetched for 1 posts with quotes", day_report)
        self.assertEqual(
            [note for note in day_payload["notes"] if note.startswith("quoters ")],
            ["quoters not fetched for 1 posts with quotes"],
        )


class PlainRepostScoreTest(unittest.TestCase):
    def test_plain_repost_likes_do_not_count(self):
        posts = [
            post("30", "2026-11-16T08:00:00Z", likes=1, text="an original"),
            post(
                "31", "2026-11-16T08:00:00Z", likes=1, text="RT @other: borrowed",
                referenced_tweets=[{"type": "retweeted", "id": "9"}],
            ),
            post(
                "32", "2026-11-16T08:00:00Z", likes=1, text="quoting them",
                referenced_tweets=[{"type": "quoted", "id": "8"}],
            ),
            post(
                "33", "2026-11-16T08:00:00Z", likes=1, text="a reply",
                in_reply_to_user_id="2", conversation_id="8",
            ),
        ]

        def prepare(folder):
            likes(folder, "30", [("101", "fan")])
            likes(folder, "31", [("102", "boost")])
            likes(folder, "32", [("103", "quotee")])
            likes(folder, "33", [("104", "replyfan")])

        report, payload = run_report(posts, "day", prepare)
        self.assertIn("fan", report)
        self.assertIn("quotee", report)
        self.assertIn("replyfan", report)
        self.assertNotIn("boost", report)
        self.assertIn("❤️ 3", report)
        self.assertEqual(payload["likes"], 3)


if __name__ == "__main__":
    unittest.main()
