#!/usr/bin/env python3
"""Unfollows 24-48h after posts, from fictional daily snapshots. Not real X data."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

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
GONE = ("a", "b", "c", "d", "e", "f", "g")


def snowflake_id(when):
    ms = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return str((ms - ca.TWITTER_EPOCH_MS) << 22)


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def users(*names):
    return [{"id": name, "username": name, "name": name} for name in names]


def write_snapshot_dir(folder):
    write_json(os.path.join(folder, "2026-11-10.json"), users("stay1", "stay2"))
    write_json(os.path.join(folder, "2026-11-11.json"), users("stay1", "stay2"))
    write_json(
        os.path.join(folder, "2026-11-12.json"),
        users("stay1", "stay2", "early", *GONE),
    )
    write_json(os.path.join(folder, "2026-11-13.json"), users("stay1", "stay2", *GONE))
    write_json(os.path.join(folder, "2026-11-14.json"), users("stay1", "stay2"))
    write_json(os.path.join(folder, "2026-11-15.json"), users("stay1", "stay2"))
    with open(os.path.join(folder, "counts.csv"), "w", encoding="utf-8") as fh:
        fh.write("date,followers_total,followers_verified,following_total,mutuals\n")
        fh.write("2026-11-12,10,0,0,0\n")
    write_json(os.path.join(folder, "pages", "2026-11-13.json"), users("trap"))
    return folder


def content_row(post_id, when, text):
    day = when.astimezone(timezone.utc).strftime("%a, %b %-d, %Y")
    return (
        f'{post_id},"{day}",{text},https://x.com/creator/status/{post_id},'
        "100,1,2,0,0,0,0,0,1,2,0,0,0"
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


def utc(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def fixture_posts():
    return [
        (snowflake_id(utc(11, 8)), utc(11, 8), "old post"),
        (snowflake_id(utc(11, 10)), utc(11, 10), "stable post"),
        (snowflake_id(utc(11, 12, 12)), utc(11, 12, 12), "spicy post"),
        (snowflake_id(utc(11, 12, 13)), utc(11, 12, 13), "follow up"),
        (snowflake_id(utc(11, 12, 14)), utc(11, 12, 14), "also this"),
        (snowflake_id(utc(11, 15)), utc(11, 15), "fresh post"),
    ]


def by_date(report):
    return {row["date"]: row for row in report["days"]}


class PostUnfollowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = write_snapshot_dir(tempfile.mkdtemp(prefix="xn-unf-snap-"))
        cls.exports = write_exports(
            tempfile.mkdtemp(prefix="xn-unf-exp-"), fixture_posts(),
            overview_days=[utc(11, d) for d in range(10, 17)],
        )
        cls.posts = ca.in_days(
            ca.load_content(cls.exports),
            utc(11, 10).date(), utc(11, 16).date(),
        )

    def test_one_line_per_day_usernames_not_ids(self):
        report = ca.post_unfollows(self.posts, self.snap)
        self.assertEqual(
            [row["date"] for row in report["days"]],
            ["2026-11-10", "2026-11-12", "2026-11-15"],
        )
        spicy = by_date(report)["2026-11-12"]
        self.assertEqual(spicy["status"], "ok")
        self.assertEqual(spicy["posts"], 3)
        self.assertEqual(spicy["gone"], list(GONE))
        self.assertEqual(spicy["gone_count"], 7)
        self.assertNotIn("early", spicy["gone"])
        self.assertEqual(by_date(report)["2026-11-10"]["gone_count"], 0)
        self.assertEqual(by_date(report)["2026-11-10"]["posts"], 1)

    def test_missing_snapshot_is_named_not_guessed(self):
        fresh = by_date(ca.post_unfollows(self.posts, self.snap))["2026-11-15"]
        self.assertEqual(fresh["status"], "no_snapshot")
        self.assertEqual(fresh["gone"], [])
        self.assertEqual(fresh["gone_count"], 0)

    def test_baseline_just_before_and_gap_is_no_snapshot(self):
        before = tempfile.mkdtemp(prefix="xn-unf-gap-")
        write_json(os.path.join(before, "2026-11-11.json"), users("stay1", "xray"))
        write_json(os.path.join(before, "2026-11-13.json"), users("stay1", "xray"))
        write_json(os.path.join(before, "2026-11-14.json"), users("stay1"))
        row = by_date(ca.post_unfollows(self.posts, before))["2026-11-12"]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["gone"], ["xray"])
        missing = tempfile.mkdtemp(prefix="xn-unf-miss-")
        write_json(os.path.join(missing, "2026-11-12.json"), users("stay1"))
        write_json(os.path.join(missing, "2026-11-14.json"), users("stay1"))
        gap = by_date(ca.post_unfollows(self.posts, missing))["2026-11-12"]
        self.assertEqual(gap["status"], "no_snapshot")
        self.assertEqual(gap["gone_count"], 0)

    def test_markdown_groups_and_caps_names(self):
        report = ca.analyze(self.exports, snapshot_dir=self.snap)
        text = ca.render_markdown(report)
        self.assertIn("Unfollows 24-48h after posts.", text)
        self.assertIn("Snapshots are daily, so the window is approximate.", text)
        self.assertIn("This is correlation, not cause.", text)
        self.assertIn(
            "12 Nov: 3 posts -> 7 unfollows 24-48h later: "
            "@a, @b, @c, @d, @e (+2 more).",
            text,
        )
        self.assertIn("10 Nov: 1 post -> 0 unfollows 24-48h later.", text)
        self.assertIn("15 Nov: 1 post: no snapshot.", text)
        self.assertNotIn("8 Nov", text)
        self.assertNotIn("old post", text)
        self.assertNotIn("@early", text)
        self.assertNotIn("@trap", text)
        self.assertNotIn("@f", text)
        unfollow_lines = [line for line in text.splitlines() if "Nov:" in line]
        self.assertEqual(len(unfollow_lines), 3)

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
        self.assertIn("@a, @b, @c, @d, @e (+2 more).", text)
        self.assertIn("no snapshot.", text)
        with open(os.path.join(out, "what_worked_week.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        spicy = by_date(doc["post_unfollows"])["2026-11-12"]
        self.assertEqual(spicy["gone"][:5], ["a", "b", "c", "d", "e"])
        cfg = os.path.join(tempfile.mkdtemp(prefix="xn-unf-cfg-"), "config.json")
        write_json(cfg, {"keyword_tags": ["Tesla"], "snapshot_dir": self.snap})
        out2 = tempfile.mkdtemp(prefix="xn-unf-out2-")
        cmd2 = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", self.exports, "--out", out2, "--config", cfg,
        ]
        result2 = subprocess.run(cmd2, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        with open(os.path.join(out2, "what_worked_week.md"), encoding="utf-8") as fh:
            self.assertIn("7 unfollows 24-48h later", fh.read())


if __name__ == "__main__":
    unittest.main()
