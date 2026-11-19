#!/usr/bin/env python3
"""Unfollows 24-48h after posts, from fictional daily snapshots. Not real X data."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import content_analytics as ca

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_HEADER = (
    "Post id,Date,Post text,Post Link,Impressions,Likes,Engagements,Bookmarks,"
    "Shares,New follows,Replies,Reposts,Profile visits,Detail Expands,"
    "URL Clicks,Hashtag Clicks,Permalink Clicks"
)
OVERVIEW_HEADER = (
    "Date,Impressions,Likes,Engagements,Bookmarks,Shares,New follows,"
    "Unfollows,Replies,Reposts,Profile visits,Create Post,Video views,Media views"
)
EXPORT_AT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def snowflake_id(when):
    ms = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return str((ms - ca.TWITTER_EPOCH_MS) << 22)


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def follower_list(*ids):
    return [
        {"id": uid, "username": f"user_{uid}", "name": f"User {uid}"}
        for uid in ids
    ]


def write_snapshot_dir(folder):
    """Three-day windows around 10 Nov and 12 Nov. 15 Nov has no +1/+2 files."""
    write_json(os.path.join(folder, "2026-11-10.json"), follower_list("8001", "8002"))
    write_json(os.path.join(folder, "2026-11-11.json"), follower_list("8001", "8002"))
    write_json(os.path.join(folder, "2026-11-12.json"), follower_list("8001", "8002", "8003", "8004"))
    write_json(os.path.join(folder, "2026-11-13.json"), follower_list("8001", "8002", "8003"))
    write_json(os.path.join(folder, "2026-11-14.json"), follower_list("8001", "8003"))
    write_json(os.path.join(folder, "2026-11-15.json"), follower_list("8001", "8003"))
    with open(os.path.join(folder, "counts.csv"), "w", encoding="utf-8") as fh:
        fh.write("date,followers_total,followers_verified,following_total,mutuals\n")
        fh.write("2026-11-10,2,0,0,0\n")
        fh.write("2026-11-12,4,0,0,0\n")
        fh.write("2026-11-15,2,0,0,0\n")
    write_json(
        os.path.join(folder, "pages", "2026-11-13.json"),
        follower_list("9999"),
    )
    return folder


def content_row(post_id, when, text):
    day = when.astimezone(timezone.utc).strftime("%a, %b %-d, %Y")
    link = f"https://x.com/creator/status/{post_id}"
    return (
        f'{post_id},"{day}",{text},{link},100,1,2,0,0,0,0,0,1,2,0,0,0'
    )


def overview_row(when):
    day = when.astimezone(timezone.utc).strftime("%a, %b %-d, %Y")
    return f'"{day}",100,1,2,0,0,0,0,0,0,1,1,0,0'


def write_exports(folder, posts, overview_days=None):
    rows = [content_row(pid, when, text) for pid, when, text in posts]
    path = os.path.join(folder, "content_7d.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(CONTENT_HEADER + "\n" + "\n".join(rows) + "\n")
    os.utime(path, (EXPORT_AT.timestamp(), EXPORT_AT.timestamp()))
    days = overview_days or [when for _pid, when, _text in posts]
    over = os.path.join(folder, "overview_7d.csv")
    with open(over, "w", encoding="utf-8") as fh:
        fh.write(OVERVIEW_HEADER + "\n")
        for when in days:
            fh.write(overview_row(when) + "\n")
    return folder


def fixture_posts():
    stable = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    spicy = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    fresh = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    return [
        (snowflake_id(stable), stable, "stable post"),
        (snowflake_id(spicy), spicy, "spicy post"),
        (snowflake_id(fresh), fresh, "fresh post"),
    ]


class PostUnfollowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = write_snapshot_dir(tempfile.mkdtemp(prefix="xn-unf-snap-"))
        cls.exports = write_exports(
            tempfile.mkdtemp(prefix="xn-unf-exp-"), fixture_posts(),
            overview_days=[
                datetime(2026, 11, d, 12, tzinfo=timezone.utc) for d in range(10, 17)
            ],
        )
        cls.posts = ca.load_content(cls.exports)
        cls.ids = [pid for pid, _when, _text in fixture_posts()]

    def test_gone_in_24_48h_not_the_earlier_drop(self):
        report = ca.post_unfollows(self.posts, self.snap)
        by_id = {row["post_id"]: row for row in report["posts"]}
        spicy = by_id[self.ids[1]]
        self.assertEqual(spicy["status"], "ok")
        self.assertEqual(spicy["baseline_date"], "2026-11-12")
        self.assertEqual(spicy["plus1_date"], "2026-11-13")
        self.assertEqual(spicy["plus2_date"], "2026-11-14")
        self.assertEqual(spicy["gone_ids"], ["8002"])
        self.assertEqual(spicy["gone_count"], 1)
        self.assertNotIn("8004", spicy["gone_ids"])

    def test_missing_snapshot_is_named_not_guessed(self):
        report = ca.post_unfollows(self.posts, self.snap)
        by_id = {row["post_id"]: row for row in report["posts"]}
        fresh = by_id[self.ids[2]]
        self.assertEqual(fresh["status"], "no_snapshot")
        self.assertEqual(fresh["gone_ids"], [])
        self.assertIn("plus1", fresh["missing"])
        self.assertIn("plus2", fresh["missing"])

    def test_zero_unfollows_when_the_window_is_stable(self):
        report = ca.post_unfollows(self.posts, self.snap)
        by_id = {row["post_id"]: row for row in report["posts"]}
        stable = by_id[self.ids[0]]
        self.assertEqual(stable["status"], "ok")
        self.assertEqual(stable["gone_ids"], [])
        self.assertEqual(stable["gone_count"], 0)

    def test_baseline_uses_snapshot_just_before_post_day(self):
        folder = tempfile.mkdtemp(prefix="xn-unf-gap-")
        write_json(os.path.join(folder, "2026-11-11.json"), follower_list("8001", "8005"))
        write_json(os.path.join(folder, "2026-11-13.json"), follower_list("8001", "8005"))
        write_json(os.path.join(folder, "2026-11-14.json"), follower_list("8001"))
        spicy = self.posts[self.posts["post_id"] == self.ids[1]]
        row = ca.post_unfollow_rows(spicy, folder)[0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["baseline_date"], "2026-11-11")
        self.assertEqual(row["gone_ids"], ["8005"])

    def test_gap_in_plus1_is_no_snapshot(self):
        folder = tempfile.mkdtemp(prefix="xn-unf-miss-")
        write_json(os.path.join(folder, "2026-11-12.json"), follower_list("8001"))
        write_json(os.path.join(folder, "2026-11-14.json"), follower_list("8001"))
        spicy = self.posts[self.posts["post_id"] == self.ids[1]]
        row = ca.post_unfollow_rows(spicy, folder)[0]
        self.assertEqual(row["status"], "no_snapshot")
        self.assertEqual(row["gone_count"], 0)
        self.assertIn("plus1", row["missing"])

    def test_markdown_is_short_and_plain(self):
        report = ca.analyze(self.exports, snapshot_dir=self.snap)
        text = ca.render_markdown(report)
        self.assertIn("Unfollows 24-48h after posts.", text)
        self.assertIn("Snapshots are daily, so the window is approximate.", text)
        self.assertIn("This is correlation, not cause.", text)
        self.assertIn('"spicy post" (12 Nov): 1 unfollow (8002).', text)
        self.assertNotIn("8004", text)
        self.assertIn('"fresh post" (15 Nov): no snapshot.', text)
        self.assertIn("1 other post had 0 unfollows.", text)
        self.assertNotIn("9999", text)
        payload = report["post_unfollows"]
        self.assertEqual(payload["note"], ca.UNFOLLOW_NOTE)
        self.assertEqual(len(payload["posts"]), 3)

    def test_skipped_without_snapshot_dir(self):
        report = ca.analyze(self.exports)
        self.assertNotIn("post_unfollows", report)
        self.assertNotIn("Unfollows 24-48h", ca.render_markdown(report))

    def test_cli_snapshot_dir_and_config_key(self):
        out = tempfile.mkdtemp(prefix="xn-unf-out-")
        cmd = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", self.exports, "--out", out, "--json",
            "--snapshot-dir", self.snap,
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(out, "what_worked_week.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"spicy post" (12 Nov): 1 unfollow (8002).', text)
        self.assertIn("no snapshot.", text)
        with open(os.path.join(out, "what_worked_week.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        spicy = next(row for row in doc["post_unfollows"]["posts"] if row["text"] == "spicy post")
        self.assertEqual(spicy["gone_ids"], ["8002"])
        cfg_dir = tempfile.mkdtemp(prefix="xn-unf-cfg-")
        cfg = os.path.join(cfg_dir, "config.json")
        write_json(cfg, {"keyword_tags": ["Tesla"], "snapshot_dir": self.snap})
        out2 = tempfile.mkdtemp(prefix="xn-unf-out2-")
        cmd2 = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", self.exports, "--out", out2,
            "--config", cfg,
        ]
        result2 = subprocess.run(cmd2, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        with open(os.path.join(out2, "what_worked_week.md"), encoding="utf-8") as fh:
            text2 = fh.read()
        self.assertIn("1 unfollow (8002).", text2)


if __name__ == "__main__":
    unittest.main()
