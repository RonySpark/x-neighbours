#!/usr/bin/env python3
"""Behaviour tests for X Analytics CSV exports. Fixtures are fictional."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timezone

import content_analytics as ca

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "analytics")


class LoadExportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = ca.load_exports(FIXTURES)
        cls.posts = cls.data["content"]
        cls.overview = cls.data["overview"]

    def test_reads_bom_and_keeps_multiline_text(self):
        self.assertGreater(len(self.posts), 0)
        self.assertIn("post_id", self.posts.columns)
        self.assertNotIn("\ufeffPost id", self.posts.columns)
        multiline = self.posts[self.posts["text"].str.contains("Tesla notes:", regex=False)]
        self.assertEqual(len(multiline), 1)
        self.assertIn("\n", multiline.iloc[0]["text"])
        self.assertIn("More range today", multiline.iloc[0]["text"])

    def test_utc_day_matches_date_column_and_midnight_shifts_in_adelaide(self):
        late = self.posts[self.posts["text"].str.contains("Late note across midnight", regex=False)]
        self.assertEqual(len(late), 1)
        row = late.iloc[0]
        self.assertEqual(row["date"], date(2026, 11, 15))
        posted = row["posted_at"]
        self.assertEqual(posted.astimezone(timezone.utc).date(), date(2026, 11, 15))
        self.assertEqual(row["adelaide_date"], date(2026, 11, 16))
        self.assertEqual(row["adelaide_weekday"], "Monday")
        decoded = ca.snowflake_utc(row["post_id"])
        self.assertEqual(decoded.astimezone(timezone.utc).date(), row["date"])

    def test_overview_loads(self):
        self.assertGreaterEqual(len(self.overview), 14)
        self.assertEqual(max(self.overview["date"]), date(2026, 11, 16))
        self.assertIn("impressions", self.overview.columns)

    def test_two_section_video_csv_does_not_break_analyze(self):
        video_path = os.path.join(FIXTURES, "video_week.csv")
        with open(video_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Your videos", text)
        self.assertIn("Uploaded on:", text)
        report = ca.analyze(FIXTURES)
        self.assertEqual(report["scorecard"]["totals"]["impressions"]["this"], 9100)
        self.assertEqual(report["consistency"]["streak"]["days"], 7)


class WeeklyScorecardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = ca.load_exports(FIXTURES)
        cls.card = ca.scorecard(data["overview"], data["content"])

    def test_totals_last_seven_versus_the_seven_before(self):
        self.assertEqual(self.card["this_week"], ["2026-11-10", "2026-11-16"])
        self.assertEqual(self.card["previous_week"], ["2026-11-03", "2026-11-09"])
        impressions = self.card["totals"]["impressions"]
        self.assertEqual(impressions["this"], 9100)
        self.assertEqual(impressions["previous"], 4900)
        self.assertEqual(impressions["change"], 4200)
        follows = self.card["totals"]["new_follows"]
        self.assertEqual(follows["this"], 19)
        self.assertEqual(follows["previous"], 7)
        self.assertEqual(self.card["from_content"]["posts"]["this"], 29)
        self.assertEqual(self.card["from_content"]["posts"]["previous"], 7)

    def test_top_and_bottom_posts(self):
        top_imp = [row["text"] for row in self.card["posts"]["impressions"]["top"]]
        bottom_imp = [row["text"] for row in self.card["posts"]["impressions"]["bottom"]]
        self.assertIn("Huge random spike", top_imp)
        self.assertEqual(bottom_imp[0], "ok")
        top_er = [row["text"] for row in self.card["posts"]["engagement_rate"]["top"]]
        self.assertIn("Is Tesla charging easier now?", top_er)
        top_fol = [row["text"] for row in self.card["posts"]["new_follows"]["top"]]
        self.assertEqual(top_fol[0], "Should Tesla build in Adelaide?")


class ContentTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = ca.load_exports(FIXTURES)
        card = ca.scorecard(data["overview"], data["content"])
        start, end = date.fromisoformat(card["this_week"][0]), date.fromisoformat(card["this_week"][1])
        posts = ca.in_days(data["content"], start, end)
        cls.types = ca.content_types(posts)
        cls.labels = [row["type"] for row in cls.types]

    def test_drops_types_with_fewer_than_three_posts_and_skips_media(self):
        self.assertNotIn("reply", self.labels)
        self.assertNotIn("media", self.labels)
        self.assertIn("Tesla", self.labels)
        self.assertIn("Adelaide", self.labels)
        self.assertIn("question", self.labels)
        self.assertIn("original", self.labels)
        self.assertIn("thread", self.labels)
        self.assertIn("link", self.labels)
        self.assertIn("long", self.labels)

    def test_medians_only_for_kept_types(self):
        tesla = next(row for row in self.types if row["type"] == "Tesla")
        self.assertGreaterEqual(tesla["posts"], 3)
        self.assertGreater(tesla["median_impressions"], 0)
        self.assertGreater(tesla["median_engagement_rate"], 0)

    def test_keywords_come_from_config(self):
        tags = ca.load_keywords(os.path.join(ROOT, "config.example.json"))
        self.assertEqual(tags, ["Tesla", "Adelaide"])


def this_week_posts():
    data = ca.load_exports(FIXTURES)
    end = ca.latest_export_date(data["overview"], data["content"])
    this_span, _prev = ca.week_windows(end)
    return data, ca.in_days(data["content"], *this_span)


class PostingTimesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _data, posts = this_week_posts()
        cls.posts = posts
        cls.times = ca.posting_times(posts)

    def test_drops_top_five_percent_and_picks_wednesday_evening(self):
        self.assertGreaterEqual(self.times["dropped"], 1)
        self.assertIsNotNone(self.times["cutoff"])
        self.assertEqual(self.times["best_weekday"]["weekday"], "Wednesday")
        self.assertGreaterEqual(self.times["best_weekday"]["posts"], 3)
        self.assertEqual(self.times["best_block"]["start_hour"], 18)
        self.assertGreaterEqual(self.times["best_block"]["posts"], 3)
        spike = self.posts[self.posts["text"].str.contains("Huge random spike", regex=False)]
        self.assertEqual(len(spike), 1)
        self.assertGreaterEqual(float(spike.iloc[0]["impressions"]), self.times["cutoff"])
        self.assertNotIn("Thursday", self.times["best_weekday"]["weekday"])


class FunnelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _data, posts = this_week_posts()
        cls.funnel = ca.funnel(posts)

    def test_overall_rates_and_top_follows(self):
        overall = self.funnel["overall"]
        self.assertGreater(overall["impressions"], 0)
        self.assertGreaterEqual(overall["expand_rate"], 0)
        self.assertLessEqual(overall["expand_rate"], 1)
        self.assertGreaterEqual(overall["visit_rate"], 0)
        self.assertLessEqual(overall["visit_rate"], 1)
        self.assertGreaterEqual(overall["follow_rate"], 0)
        self.assertLessEqual(overall["follow_rate"], 1)
        self.assertEqual(
            self.funnel["top_follows"][0]["text"], "Should Tesla build in Adelaide?")
        self.assertEqual(self.funnel["top_follows"][0]["new_follows"], 15)

    def test_type_funnels_skip_small_types(self):
        labels = [row["type"] for row in self.funnel["by_type"]]
        self.assertNotIn("reply", labels)
        tesla = next(row for row in self.funnel["by_type"] if row["type"] == "Tesla")
        self.assertGreaterEqual(tesla["posts"], 3)
        self.assertGreater(tesla["new_follows"], 0)


class ConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, _posts = this_week_posts()
        cls.report = ca.consistency(data["overview"], data["content"])

    def test_streak_posts_per_day_and_examples(self):
        self.assertEqual(self.report["streak"]["days"], 7)
        self.assertEqual(self.report["streak"]["until"], "2026-11-16")
        self.assertAlmostEqual(self.report["posts_per_day"], 29 / 7)
        self.assertEqual(self.report["posts"]["this"], 29)
        self.assertEqual(self.report["posts"]["previous"], 7)
        self.assertEqual(self.report["do_more"]["text"], "Is Tesla charging easier now?")
        self.assertEqual(self.report["underperformer"]["text"], "Huge random spike")


class ReportCliTest(unittest.TestCase):
    def test_writes_markdown_and_optional_json(self):
        out = tempfile.mkdtemp(prefix="xn-what-worked-")
        cmd = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", FIXTURES, "--out", out, "--json",
            "--config", os.path.join(ROOT, "config.example.json"),
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        md_path = os.path.join(out, "what_worked_week.md")
        json_path = os.path.join(out, "what_worked_week.json")
        self.assertTrue(os.path.isfile(md_path))
        self.assertTrue(os.path.isfile(json_path))
        with open(md_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("## What worked this week"))
        self.assertIn("Wednesday", text)
        self.assertIn("Is Tesla charging easier now?", text)
        self.assertIn("Huge random spike", text)
        lines = [line for line in text.strip().splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 8)
        self.assertLessEqual(len(lines), 22)
        with open(json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["consistency"]["streak"]["days"], 7)
        self.assertEqual(doc["scorecard"]["totals"]["impressions"]["this"], 9100)


CONTENT_HEADER = (
    "Post id,Date,Post text,Post Link,Impressions,Likes,Engagements,Bookmarks,"
    "Shares,New follows,Replies,Reposts,Profile visits,Detail Expands,"
    "URL Clicks,Hashtag Clicks,Permalink Clicks"
)
OVERVIEW_HEADER = (
    "Date,Impressions,Likes,Engagements,Bookmarks,Shares,New follows,"
    "Unfollows,Replies,Reposts,Profile visits,Create Post,Video views,Media views"
)
OVERLAP_DATE = '"Wed, Nov 11, 2026"'
OVERLAP_ID = "2120660341355446282"
OLDER_ONLY_ID = "2121370017592246293"


def _write_csv(folder, name, header, rows, mtime):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n" + "\n".join(rows) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def _content_row(post_id, text, impressions, follows):
    return (
        f"{post_id},{OVERLAP_DATE},{text},https://x.com/c/status/{post_id},"
        f"{impressions},1,1,0,0,{follows},0,0,0,0,0,0,0"
    )


def overlap_exports(content_3m_mtime=1_700_000_000, content_7d_mtime=1_700_000_100):
    folder = tempfile.mkdtemp(prefix="xn-overlap-")
    _write_csv(
        folder, "content_3m.csv", CONTENT_HEADER,
        [
            _content_row(OVERLAP_ID, "stale 3m", 99999, 0),
            _content_row(OLDER_ONLY_ID, "3m only", 40, 0),
        ],
        content_3m_mtime,
    )
    _write_csv(
        folder, "content_7d.csv", CONTENT_HEADER,
        [_content_row(OVERLAP_ID, "fresh 7d", 100, 9)],
        content_7d_mtime,
    )
    _write_csv(
        folder, "overview_3m.csv", OVERVIEW_HEADER,
        [f"{OVERLAP_DATE},70,0,0,0,0,0,0,0,0,0,0,0,0"],
        content_3m_mtime,
    )
    _write_csv(
        folder, "overview_7d.csv", OVERVIEW_HEADER,
        [f"{OVERLAP_DATE},6993,0,0,0,0,0,0,0,0,0,0,0,0"],
        content_7d_mtime,
    )
    return folder


class OverlapExportsTest(unittest.TestCase):
    def test_keeps_row_from_newest_file_not_sorted_name(self):
        folder = overlap_exports()
        data = ca.load_exports(folder)
        posts = data["content"].set_index("post_id")
        self.assertEqual(int(posts.loc[OVERLAP_ID, "impressions"]), 100)
        self.assertEqual(int(posts.loc[OVERLAP_ID, "new_follows"]), 9)
        self.assertEqual(posts.loc[OVERLAP_ID, "text"], "fresh 7d")
        self.assertEqual(int(posts.loc[OLDER_ONLY_ID, "impressions"]), 40)
        self.assertEqual(int(data["overview"].iloc[0]["impressions"]), 6993)
        card = ca.scorecard(data["overview"], data["content"])
        self.assertEqual(card["posts"]["impressions"]["top"][0]["text"], "fresh 7d")
        self.assertEqual(card["posts"]["impressions"]["top"][0]["impressions"], 100)
        self.assertEqual(card["totals"]["impressions"]["this"], 6993)

    def test_newer_3m_file_wins_when_it_is_the_latest_export(self):
        folder = overlap_exports(content_3m_mtime=1_700_000_200, content_7d_mtime=1_700_000_100)
        data = ca.load_exports(folder)
        row = data["content"].set_index("post_id").loc[OVERLAP_ID]
        self.assertEqual(int(row["impressions"]), 99999)
        self.assertEqual(int(data["overview"].iloc[0]["impressions"]), 70)


class BlankPostIdTest(unittest.TestCase):
    def test_skips_blank_and_non_numeric_ids_without_mangling_digits(self):
        folder = tempfile.mkdtemp(prefix="xn-blank-id-")
        _write_csv(
            folder, "content_7d.csv", CONTENT_HEADER,
            [
                _content_row("", "blank row", 10, 0),
                _content_row("not-an-id", "junk", 11, 0),
                _content_row(OVERLAP_ID, "ok", 100, 9),
            ],
            1_700_000_100,
        )
        posts = ca.load_content(folder)
        self.assertEqual(list(posts["post_id"]), [OVERLAP_ID])
        self.assertNotIn("e+", str(posts.iloc[0]["post_id"]).lower())
        self.assertEqual(ca.snowflake_utc(posts.iloc[0]["post_id"]).tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
