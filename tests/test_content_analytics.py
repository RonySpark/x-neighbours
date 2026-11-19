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

import pandas as pd

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


class ClassifyPostTest(unittest.TestCase):
    def test_url_query_is_not_a_question(self):
        labels = ca.classify_post("Notes at https://example.com/page?q=1", [])
        self.assertNotIn("question", labels)
        self.assertIn("question", ca.classify_post("Is charging easier now?", []))

    def test_t_co_does_not_match_other_hosts(self):
        labels = ca.classify_post("See list.com for the names", [])
        self.assertNotIn("link", labels)
        self.assertIn("link", ca.classify_post("See t.co/abc", []))

    def test_fraction_is_not_a_thread(self):
        labels = ca.classify_post("3/4 of the way there", [])
        self.assertNotIn("thread", labels)
        self.assertIn("thread", ca.classify_post("1/ Three notes on posting", []))


def this_week_posts():
    data = ca.load_exports(FIXTURES)
    end = ca.latest_export_date(data["overview"], data["content"])
    this_span, _prev = ca.week_windows(end)
    return data, ca.in_days(data["content"], *this_span)


def _time_posts(rows):
    """Synthetic rows: (weekday, hour, impressions, text). No real X data."""
    return pd.DataFrame([
        {
            "adelaide_weekday": weekday,
            "adelaide_hour": hour,
            "impressions": impressions,
            "engagement_rate": 0.1,
            "text": text,
        }
        for weekday, hour, impressions, text in rows
    ])


def _times_report(times):
    """Minimal report so render_markdown can print best-time lines."""
    zero = {"this": 0, "previous": 0, "change": 0, "pct": None}
    return {
        "scorecard": {
            "this_week": ["2026-11-10", "2026-11-16"],
            "previous_week": ["2026-11-03", "2026-11-09"],
            "totals": {"impressions": zero, "new_follows": zero},
            "posts": {
                "impressions": {"top": [], "bottom": []},
                "new_follows": {"top": []},
            },
        },
        "types": [],
        "times": times,
        "funnel": {"overall": {"expand_rate": 0, "visit_rate": 0, "follow_rate": 0}},
        "consistency": {
            "posts": {"this": 0, "previous": 0},
            "posts_per_day": 0.0,
            "streak": {"days": 0},
        },
    }


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

    def test_six_posts_does_not_drop_the_winning_wednesday(self):
        posts = _time_posts([
            ("Wednesday", 18, 5000, "wed one"),
            ("Wednesday", 18, 5100, "wed two"),
            ("Wednesday", 18, 4900, "wed three"),
            ("Friday", 18, 100, "fri one"),
            ("Friday", 18, 110, "fri two"),
            ("Friday", 18, 90, "fri three"),
        ])
        times = ca.posting_times(posts)
        self.assertEqual(times["dropped"], 0)
        self.assertEqual(times["best_weekday"]["weekday"], "Wednesday")
        self.assertEqual(times["best_weekday"]["posts"], 3)
        self.assertEqual(times["sample_posts"], 3)

    def test_three_posts_still_report_a_best_time(self):
        posts = _time_posts([
            ("Wednesday", 18, 5000, "a"),
            ("Wednesday", 18, 5100, "b"),
            ("Wednesday", 18, 4900, "c"),
        ])
        times = ca.posting_times(posts)
        self.assertEqual(times["dropped"], 0)
        self.assertEqual(times["kept"], 3)
        self.assertEqual(times["best_weekday"]["weekday"], "Wednesday")
        self.assertEqual(times["best_block"]["start_hour"], 18)
        phrase = ca.best_time_phrase(times)
        self.assertIn("Wednesday 18:00-20:00 Adelaide (3 posts, small sample)", phrase)

    def test_block_starts_at_an_hour_that_has_posts(self):
        posts = _time_posts([
            ("Monday", 20, 1000, "a"),
            ("Tuesday", 20, 1000, "b"),
            ("Wednesday", 20, 1000, "c"),
        ])
        times = ca.posting_times(posts)
        self.assertEqual(times["best_block"]["start_hour"], 20)
        self.assertEqual(times["best_block"]["label"], "20:00-22:00")
        self.assertNotEqual(times["best_block"]["start_hour"], 18)
        self.assertIn("20:00-22:00", ca.best_time_phrase(times))

    def test_per_type_sample_sizes_and_floor(self):
        posts = _time_posts([
            ("Tuesday", 4, 8000, "@bot short one"),
            ("Tuesday", 4, 8100, "@bot short two"),
            ("Tuesday", 4, 7900, "@bot short three"),
            ("Tuesday", 4, 8050, "@bot short four"),
            ("Tuesday", 4, 7950, "@bot short five"),
            ("Friday", 9, 400, "https://example.com/one"),
            ("Friday", 9, 410, "https://example.com/two"),
            ("Friday", 9, 390, "https://example.com/three"),
            ("Saturday", 12, 50, "x" * 210),
            ("Saturday", 12, 55, "y" * 210),
        ])
        times = ca.posting_times(posts)
        by_type = {row["type"]: row for row in times["by_type"]}
        self.assertIn("short", by_type)
        self.assertIn("reply", by_type)
        self.assertIn("link", by_type)
        self.assertNotIn("long", by_type)
        self.assertEqual(by_type["short"]["sample_posts"], 5)
        self.assertEqual(by_type["short"]["best_weekday"]["weekday"], "Tuesday")
        self.assertEqual(by_type["short"]["best_block"]["start_hour"], 4)
        self.assertEqual(by_type["reply"]["sample_posts"], 5)
        self.assertEqual(by_type["link"]["sample_posts"], 3)
        self.assertEqual(by_type["link"]["best_weekday"]["weekday"], "Friday")
        self.assertIn("(5 posts)", ca.best_time_phrase(by_type["reply"]))
        self.assertNotIn("small sample", ca.best_time_phrase(by_type["reply"]))
        self.assertIn("(3 posts, small sample)", ca.best_time_phrase(by_type["link"]))
        text = ca.render_markdown(_times_report(times))
        self.assertIn("Best time to post: Tuesday 04:00-06:00 Adelaide (5 posts).", text)
        self.assertEqual(text.count("By type:"), 1)
        by_line = text.split("By type:")[1].split("\n")[0]
        self.assertIn("Reply Tuesday 04:00-06:00 Adelaide (5 posts)", by_line)
        self.assertIn("Short Tuesday 04:00-06:00 Adelaide (5 posts)", by_line)
        self.assertNotIn("Long", by_line)
        self.assertNotIn("Link", by_line)

    def test_separately_strong_weekday_and_block_are_not_combined(self):
        long = "x" * 210
        posts = _time_posts([
            ("Tuesday", 8, 3000, long),
            ("Tuesday", 10, 3000, long),
            ("Tuesday", 16, 3000, long),
            ("Tuesday", 19, 3000, long),
            ("Tuesday", 13, 3000, long),
            ("Friday", 13, 400, long),
            ("Friday", 13, 400, long),
            ("Friday", 14, 400, long),
            ("Friday", 14, 400, long),
        ])
        times = ca.posting_times(posts)
        self.assertEqual(times["best_weekday"]["weekday"], "Tuesday")
        self.assertGreaterEqual(times["best_weekday"]["posts"], 4)
        self.assertEqual(times["best_block"]["label"], "13:00-15:00")
        self.assertGreaterEqual(times["best_block"]["posts"], 4)
        self.assertEqual(times["sample_posts"], 1)
        phrase = ca.best_time_phrase(times)
        self.assertEqual(
            phrase,
            "Tuesday (5 posts) or 13:00-15:00 (5 posts), "
            "not enough posts in both to combine",
        )
        self.assertNotIn("Tuesday 13:00-15:00", phrase)
        self.assertNotIn("1 posts", phrase)
        self.assertEqual(ca._count_note(1), "(1 post, small sample)")
        by_type = {row["type"]: row for row in times["by_type"]}
        self.assertIn("long", by_type)
        self.assertEqual(by_type["long"]["sample_posts"], 1)
        self.assertIn("or", ca.best_time_phrase(by_type["long"]))
        text = ca.render_markdown(_times_report(times))
        self.assertIn(
            "Best time to post: Tuesday (5 posts) or 13:00-15:00 (5 posts), "
            "not enough posts in both to combine.",
            text,
        )
        self.assertIn("Long Tuesday (5 posts) or 13:00-15:00 (5 posts)", text)


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

    def test_gap_after_last_original_is_not_a_current_streak(self):
        """Synthetic only. Originals through 14 Nov, export ends 16 Nov."""
        def row(day, text):
            return {
                "date": date(2026, 11, day),
                "adelaide_date": date(2026, 11, day),
                "posted_at": datetime(2026, 11, day, 12, tzinfo=timezone.utc),
                "text": text,
                "post_id": str(day),
                "impressions": 10,
                "engagements": 1,
                "engagement_rate": 0.1,
                "new_follows": 0,
            }
        content = pd.DataFrame(
            [row(day, "original") for day in range(10, 15)]
            + [row(15, "@bot reply"), row(16, "@bot later")]
        )
        overview = pd.DataFrame()
        self.assertEqual(ca.latest_export_date(overview, content), date(2026, 11, 16))
        self.assertEqual(ca.consistency(overview, content)["streak"]["days"], 0)


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


def _pick_row(post_id, posted_at, impressions, engagement_rate, text):
    return {
        "post_id": post_id,
        "posted_at": posted_at,
        "text": text,
        "impressions": impressions,
        "engagements": int(impressions * engagement_rate),
        "engagement_rate": engagement_rate,
        "new_follows": 0,
    }


def _tied_pick_posts(order):
    """Six fictional posts. Two share top impressions, two share the floor, two notable posts share a low rate."""
    older = datetime(2026, 11, 10, tzinfo=timezone.utc)
    newer = datetime(2026, 11, 16, tzinfo=timezone.utc)
    mid = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = {
        "high_old": _pick_row("10", older, 100, 0.05, "high old"),
        "high_new": _pick_row("20", newer, 100, 0.05, "high new"),
        "star": _pick_row("15", mid, 90, 0.50, "star"),
        "mid": _pick_row("25", mid, 50, 0.10, "mid"),
        "quiet_old": _pick_row("30", older, 10, 0.01, "quiet old"),
        "quiet_new": _pick_row("40", newer, 10, 0.01, "quiet new"),
    }
    return pd.DataFrame([rows[name] for name in order])


class TiedPickTest(unittest.TestCase):
    def test_tied_picks_ignore_row_order(self):
        orders = [
            ("high_old", "high_new", "star", "mid", "quiet_old", "quiet_new"),
            ("quiet_new", "quiet_old", "mid", "star", "high_new", "high_old"),
        ]
        picks = []
        for order in orders:
            posts = _tied_pick_posts(order)
            top, bottom = ca.ranked_posts(posts, "impressions")
            examples = ca.pick_examples(posts)
            picks.append({
                "best": top[0]["post_id"],
                "quietest": bottom[0]["post_id"],
                "ease_off": examples["underperformer"]["post_id"],
                "do_more": examples["do_more"]["post_id"],
            })
        self.assertEqual(picks[0], picks[1])
        self.assertEqual(picks[0]["best"], "20")
        self.assertEqual(picks[0]["quietest"], "40")
        self.assertEqual(picks[0]["ease_off"], "20")
        self.assertEqual(picks[0]["do_more"], "15")


class WeekEndFromContentTest(unittest.TestCase):
    def test_overview_ending_later_keeps_best_post_in_this_week(self):
        """Synthetic only. Overview ending a day later must not drop this week's best post."""
        content = pd.DataFrame([
            {
                "date": date(2026, 11, 10), "post_id": "10",
                "posted_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
                "text": "best of the week", "impressions": 5000, "engagements": 50,
                "engagement_rate": 0.01, "new_follows": 0,
            },
            {
                "date": date(2026, 11, 16), "post_id": "20",
                "posted_at": datetime(2026, 11, 16, tzinfo=timezone.utc),
                "text": "later quieter", "impressions": 50, "engagements": 1,
                "engagement_rate": 0.02, "new_follows": 0,
            },
        ])
        overview = pd.DataFrame({"date": [date(2026, 11, d) for d in range(10, 18)]})
        card = ca.scorecard(overview, content)
        self.assertEqual(card["this_week"], ["2026-11-10", "2026-11-16"])
        self.assertEqual(card["posts"]["impressions"]["top"][0]["text"], "best of the week")
        self.assertEqual(ca.latest_export_date(overview, pd.DataFrame()), date(2026, 11, 17))


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
