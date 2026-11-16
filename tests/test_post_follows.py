#!/usr/bin/env python3
"""Per-post follows CSV for the 48-hour review. Fixtures are fictional."""
import csv
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import content_analytics as ca

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = (
    "Post id,Date,Post text,Post Link,Impressions,Likes,Engagements,Bookmarks,"
    "Shares,New follows,Replies,Reposts,Profile visits,Detail Expands,"
    "URL Clicks,Hashtag Clicks,Permalink Clicks"
)
EXPORT_AT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def snowflake_id(when):
    ms = int(when.astimezone(timezone.utc).timestamp() * 1000)
    return str((ms - ca.TWITTER_EPOCH_MS) << 22)


def content_row(post_id, when, text, impressions, visits, follows, replies, likes, reposts, quotes=None):
    day = when.astimezone(timezone.utc).strftime("%a, %b %-d, %Y")
    link = f"https://x.com/creator/status/{post_id}"
    row = (
        f'{post_id},"{day}",{text},{link},{impressions},{likes},10,0,0,'
        f"{follows},{replies},{reposts},{visits},0,0,0,0"
    )
    if quotes is not None:
        row += f",{quotes}"
    return row


def write_export(folder, rows, header=HEADER, mtime=None):
    path = os.path.join(folder, "content_7d.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n" + "\n".join(rows) + "\n")
    when = mtime or EXPORT_AT
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


def three_posts(quotes=False):
    young = EXPORT_AT - timedelta(hours=24)
    mid = EXPORT_AT - timedelta(hours=60)
    old = EXPORT_AT - timedelta(hours=96)
    ids = snowflake_id(young), snowflake_id(mid), snowflake_id(old)
    header = HEADER + ",Quotes" if quotes else HEADER
    rows = [
        content_row(ids[0], young, "fresh", 100, 8, 5, 2, 4, 1, quotes=3 if quotes else None),
        content_row(ids[1], mid, "middle", 80, 3, 2, 1, 2, 0, quotes=1 if quotes else None),
        content_row(ids[2], old, "old", 40, 1, 9, 0, 1, 0, quotes=0 if quotes else None),
    ]
    return ids, header, rows


class PostFollowsCsvTest(unittest.TestCase):
    def test_48h_note_and_constructed_urls(self):
        folder = tempfile.mkdtemp(prefix="xn-follows-")
        ids, header, rows = three_posts()
        write_export(folder, rows, header)
        posts = ca.load_content(folder)
        dest = os.path.join(folder, "post_follows.csv")
        ca.write_post_follows_csv(dest, posts, ca.export_mtime(folder))
        with open(dest, newline="", encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        self.assertEqual(
            list(got[0].keys()),
            [
                "post_id", "post_url", "posted_at_adelaide", "age_hours_at_export",
                "impressions", "profile_visits", "new_follows", "replies",
                "likes", "reposts", "follows_window_note",
            ],
        )
        by_id = {row["post_id"]: row for row in got}
        self.assertNotIn("quotes", got[0])
        young, mid, old = ids
        self.assertEqual(by_id[young]["post_url"], f"https://x.com/ronyspark/status/{young}")
        self.assertEqual(float(by_id[young]["age_hours_at_export"]), 24.0)
        self.assertEqual(by_id[young]["follows_window_note"], "within_48h")
        self.assertEqual(by_id[young]["new_follows"], "5")
        self.assertEqual(float(by_id[mid]["age_hours_at_export"]), 60.0)
        self.assertEqual(by_id[mid]["follows_window_note"], "lifetime_to_export")
        self.assertEqual(by_id[old]["follows_window_note"], "lifetime_to_export")
        self.assertIn("+", by_id[young]["posted_at_adelaide"])

    def test_quotes_column_only_when_export_has_it(self):
        folder = tempfile.mkdtemp(prefix="xn-quotes-")
        ids, header, rows = three_posts(quotes=True)
        write_export(folder, rows, header)
        dest = os.path.join(folder, "with_quotes.csv")
        ca.write_post_follows_csv(dest, ca.load_content(folder), ca.export_mtime(folder))
        with open(dest, newline="", encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        self.assertEqual(
            list(got[0].keys())[7:10],
            ["replies", "quotes", "likes"],
        )
        self.assertEqual({row["post_id"]: row["quotes"] for row in got}[ids[0]], "3")


class ReviewLogTest(unittest.TestCase):
    def test_query_strings_and_column_order_preserved(self):
        folder = tempfile.mkdtemp(prefix="xn-review-")
        ids, header, rows = three_posts()
        write_export(folder, rows, header)
        young, mid, old = ids
        review = os.path.join(folder, "review.csv")
        with open(review, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["notes", "post_url", "keep_me"])
            writer.writeheader()
            writer.writerows([
                {"notes": "first", "post_url": f"https://x.com/ronyspark/status/{young}?s=20", "keep_me": "a"},
                {"notes": "second", "post_url": f"https://x.com/ronyspark/status/{mid}/", "keep_me": "b"},
                {"notes": "third", "post_url": f"https://x.com/ronyspark/status/{old}?s=20", "keep_me": "c"},
                {"notes": "miss", "post_url": "https://x.com/other/status/1?s=20", "keep_me": "d"},
            ])
        ca.fill_review_log(review, ca.load_content(folder), ca.export_mtime(folder))
        with open(review, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames)
            got = list(reader)
        self.assertEqual(fieldnames, ["notes", "post_url", "keep_me", "follows_48h"])
        self.assertEqual([row["notes"] for row in got], ["first", "second", "third", "miss"])
        self.assertEqual([row["keep_me"] for row in got], ["a", "b", "c", "d"])
        self.assertEqual(got[0]["follows_48h"], "5")
        self.assertEqual(got[1]["follows_48h"], "2")
        self.assertEqual(got[2]["follows_48h"], "")
        self.assertEqual(got[3]["follows_48h"], "")
        self.assertTrue(got[0]["post_url"].endswith("?s=20"))

    def test_cli_writes_csv_and_fills_review_log(self):
        folder = tempfile.mkdtemp(prefix="xn-cli-follows-")
        ids, header, rows = three_posts()
        write_export(folder, rows, header)
        out = tempfile.mkdtemp(prefix="xn-cli-out-")
        dest = os.path.join(out, "post_follows.csv")
        review = os.path.join(out, "review.csv")
        with open(review, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["post_url"])
            writer.writeheader()
            writer.writerow({"post_url": f"https://x.com/ronyspark/status/{ids[0]}?s=20"})
        cmd = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", folder, "--out", out,
            "--post-follows-csv", dest, "--review-log", review,
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(dest, newline="", encoding="utf-8") as fh:
            follows = list(csv.DictReader(fh))
        self.assertEqual(len(follows), 3)
        self.assertEqual(follows[0]["follows_window_note"], "within_48h")
        with open(review, newline="", encoding="utf-8") as fh:
            log = list(csv.DictReader(fh))
        self.assertEqual(log[0]["follows_48h"], "5")


if __name__ == "__main__":
    unittest.main()
