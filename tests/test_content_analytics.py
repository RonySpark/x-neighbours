#!/usr/bin/env python3
"""Behaviour tests for X Analytics CSV exports. Fixtures are fictional."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

import content_analytics as ca

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "analytics")
API_FIXTURES = os.path.join(ROOT, "tests", "fixtures", "api")


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
        self.assertNotIn("Followers:", text)
        lines = [line for line in text.strip().splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 8)
        self.assertLessEqual(len(lines), 22)
        with open(json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["consistency"]["streak"]["days"], 7)
        self.assertEqual(doc["scorecard"]["totals"]["impressions"]["this"], 9100)
        self.assertNotIn("follower_share", doc)

    def test_data_flag_adds_follower_share(self):
        out = tempfile.mkdtemp(prefix="xn-what-worked-data-")
        cmd = [
            sys.executable, os.path.join(ROOT, "content_analytics.py"),
            "--exports", FIXTURES, "--data", API_FIXTURES, "--out", out, "--json",
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(out, "what_worked_week.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(
            "Followers (list from 16 Nov): 40% of people who engaged (60% not), 30% of actions.",
            text,
        )
        self.assertIn("Likes 25%, reposts 50%, replies 25%.", text)
        self.assertNotIn("follower list is", text)
        self.assertIn("1 non-follower engaged 2+ times (likely follow-back).", text)
        with open(os.path.join(out, "what_worked_week.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["follower_share"]["repeat_non_followers"], 1)
        self.assertEqual(doc["follower_share"]["follower_snapshot_age_days"], 0)


class FollowerShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = ca.analyze(FIXTURES, data_dir=API_FIXTURES, me_id="100")
        cls.share = cls.report["follower_share"]

    def test_skipped_when_no_data_folder(self):
        report = ca.analyze(FIXTURES)
        self.assertNotIn("follower_share", report)
        self.assertNotIn("Followers:", ca.render_markdown(report))

    def test_owner_id_comes_from_posts_not_config_placeholder(self):
        report = ca.analyze(FIXTURES, data_dir=API_FIXTURES, me_id="your_user_id")
        self.assertEqual(report["follower_share"]["overall"]["people"]["total"], 5)

    def test_overall_people_and_actions(self):
        people = self.share["overall"]["people"]
        actions = self.share["overall"]["actions"]
        self.assertEqual(people["followers"], 2)
        self.assertEqual(people["non_followers"], 3)
        self.assertEqual(people["total"], 5)
        self.assertAlmostEqual(people["follower_share"], 0.4)
        self.assertEqual(actions["followers"], 3)
        self.assertEqual(actions["non_followers"], 7)
        self.assertEqual(actions["total"], 10)
        self.assertAlmostEqual(actions["follower_share"], 0.3)

    def test_split_by_likes_reposts_replies(self):
        likes = self.share["likes"]
        self.assertEqual(likes["people"]["followers"], 1)
        self.assertEqual(likes["people"]["non_followers"], 2)
        self.assertEqual(likes["actions"]["followers"], 1)
        self.assertEqual(likes["actions"]["total"], 4)
        reposts = self.share["reposts"]
        self.assertEqual(reposts["people"]["followers"], 1)
        self.assertEqual(reposts["people"]["non_followers"], 1)
        self.assertEqual(reposts["actions"]["total"], 2)
        replies = self.share["replies"]
        self.assertEqual(replies["people"]["followers"], 1)
        self.assertEqual(replies["people"]["non_followers"], 2)
        self.assertEqual(replies["actions"]["followers"], 1)
        self.assertEqual(replies["actions"]["total"], 4)

    def test_excludes_last_week_and_non_replies(self):
        neighbours = ca.load_neighbour_data(API_FIXTURES, "100")
        self.assertIn("101", neighbours.likes["5003"])
        likes = self.share["likes"]["actions"]["total"]
        self.assertEqual(likes, 4)
        replies = self.share["replies"]
        self.assertEqual(replies["people"]["total"], 3)
        self.assertEqual(self.share["repeat_non_followers"], 1)

    def test_markdown_and_json_include_two_lines(self):
        text = ca.render_markdown(self.report)
        self.assertIn(
            "Followers (list from 16 Nov): 40% of people who engaged (60% not), 30% of actions. "
            "Likes 25%, reposts 50%, replies 25%.",
            text,
        )
        self.assertNotIn("follower list is", text)
        self.assertIn("1 non-follower engaged 2+ times (likely follow-back).", text)
        payload = ca.json_ready(self.report)
        self.assertEqual(payload["follower_share"]["repeat_non_followers"], 1)
        self.assertEqual(payload["follower_share"]["likes"]["actions"]["total"], 4)
        self.assertEqual(payload["follower_share"]["snapshot_date"], "2026-11-16")
        self.assertEqual(payload["follower_share"]["follower_snapshot_age_days"], 0)

    def test_fresh_mtime_has_date_and_no_warning(self):
        folder = _api_copy(fetched_at=None, mtime=datetime(2026, 11, 16, 12, tzinfo=timezone.utc))
        report = ca.analyze(FIXTURES, data_dir=folder, me_id="100")
        share = report["follower_share"]
        self.assertEqual(share["snapshot_date"], "2026-11-16")
        self.assertEqual(share["follower_snapshot_age_days"], 0)
        text = ca.render_markdown(report)
        self.assertIn("Followers (list from 16 Nov):", text)
        self.assertNotIn("follower list is", text)

    def test_stale_snapshot_warns_and_sets_age_days(self):
        folder = _api_copy(fetched_at="2026-11-01T09:00:00Z")
        report = ca.analyze(FIXTURES, data_dir=folder, me_id="100")
        share = report["follower_share"]
        self.assertEqual(share["snapshot_date"], "2026-11-01")
        self.assertEqual(share["follower_snapshot_age_days"], 15)
        text = ca.render_markdown(report)
        self.assertIn("Followers (list from 1 Nov):", text)
        self.assertIn(
            "(follower list is 15 days old, so refresh it for accurate numbers)",
            text,
        )


def _api_copy(fetched_at=False, mtime=None):
    """Copy the API fixtures. fetched_at=None strips the saved time; a string sets it."""
    dest = tempfile.mkdtemp(prefix="xn-api-")
    for name in os.listdir(API_FIXTURES):
        src = os.path.join(API_FIXTURES, name)
        dst = os.path.join(dest, name)
        if name.startswith("followers_") and fetched_at is not False:
            with open(src, encoding="utf-8") as fh:
                doc = json.load(fh)
            if fetched_at is None:
                doc.pop("fetched_at", None)
            else:
                doc["fetched_at"] = fetched_at
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
        else:
            shutil.copy(src, dst)
        if mtime is not None and name.startswith("followers_"):
            epoch = mtime.timestamp()
            os.utime(dst, (epoch, epoch))
    return dest


if __name__ == "__main__":
    unittest.main()
