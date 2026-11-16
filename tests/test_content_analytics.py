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


if __name__ == "__main__":
    unittest.main()
