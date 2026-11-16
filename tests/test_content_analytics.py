#!/usr/bin/env python3
"""Behaviour tests for X Analytics CSV exports. Fixtures are fictional."""
import os
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
        cls.video = cls.data["video"]

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

    def test_overview_and_optional_video_load(self):
        self.assertGreaterEqual(len(self.overview), 14)
        self.assertEqual(max(self.overview["date"]), date(2026, 11, 16))
        self.assertIn("impressions", self.overview.columns)
        self.assertIn("date", self.video.columns)
        self.assertNotIn("Video overview", self.video.columns)
        self.assertGreater(len(self.video), 0)
        self.assertGreater(int(self.video["views"].sum()), 0)

    def test_missing_video_is_ok(self):
        import tempfile
        folder = tempfile.mkdtemp(prefix="xn-analytics-")
        overview_src = os.path.join(FIXTURES, "overview_week.csv")
        with open(overview_src, encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(folder, "overview_week.csv"), "w", encoding="utf-8") as fh:
            fh.write(text)
        loaded = ca.load_exports(folder)
        self.assertTrue(loaded["video"].empty)
        self.assertGreater(len(loaded["overview"]), 0)


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


if __name__ == "__main__":
    unittest.main()
