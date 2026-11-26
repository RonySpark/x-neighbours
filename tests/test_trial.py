#!/usr/bin/env python3
"""Welcome tour: five originals or quotes, no stranger marks, a time estimate.

Handles, names, and ids here are fictional. Nothing in this file is a real account.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-11-16T12:00:00Z"


def starter(pid, created, likes=1, reposts=1, quotes=1, replies=0):
    return {
        "id": pid,
        "created_at": created,
        "conversation_id": pid,
        "text": f"Original {pid}",
        "public_metrics": {
            "like_count": likes,
            "repost_count": reposts,
            "quote_count": quotes,
            "reply_count": replies,
        },
    }


def owner_reply(pid, created):
    return {
        "id": pid,
        "created_at": created,
        "conversation_id": "s1",
        "text": "A reply, even with many likes.",
        "referenced_tweets": [{"type": "replied_to", "id": "s1"}],
        "public_metrics": {"like_count": 20, "repost_count": 4, "quote_count": 2},
    }


class TrialPlanTest(unittest.TestCase):
    def _posts(self):
        rows = []
        for index in range(1, 9):
            hour = 8 + index
            rows.append(starter(f"s{index}", f"2026-11-16T{hour:02d}:00:00Z"))
        rows.append(owner_reply("r9", "2026-11-16T18:30:00Z"))
        rows.append({
            "id": "q1",
            "created_at": "2026-11-16T19:00:00Z",
            "conversation_id": "quoted-other",
            "text": "A quote.",
            "referenced_tweets": [{"type": "quoted", "id": "quoted-other"}],
            "public_metrics": {"like_count": 1, "repost_count": 0, "quote_count": 0},
        })
        return rows

    def test_plan_keeps_originals_and_quotes_and_caps_at_five(self):
        self.assertEqual(xn.TRIAL_POST_COUNT, 5)
        self.assertEqual(xn.TRIAL_READ_BUDGET, 15)
        chosen = xn.select_trial_posts(self._posts(), limit=5)
        ids = [post["id"] for post in chosen]
        self.assertEqual(ids[0], "q1")
        self.assertNotIn("r9", ids)
        self.assertEqual(len(ids), 5)
        self.assertEqual(ids[1:], ["s8", "s7", "s6", "s5"])
        short = xn.select_trial_posts(self._posts(), limit=3)
        self.assertEqual([post["id"] for post in short], ["q1", "s8", "s7"])

    def test_full_engagement_stays_inside_the_read_budget(self):
        rows = [row for row in self._posts() if row["id"] != "q1"]
        plan = xn.trial_plan(rows, limit=5, budget=15)
        self.assertEqual(len(plan["posts"]), 5)
        self.assertEqual(plan["reply_pages"], 0)
        self.assertLessEqual(plan["read_count"], 15)
        self.assertEqual(plan["read_count"], 15)
        self.assertFalse(plan["within_budget"])
        tools = [row["tool"] for row in plan["reads"]]
        self.assertEqual(tools[0], "get_users_posts")
        self.assertNotIn("search_posts_all", tools)
        self.assertEqual(tools.count("get_posts_liking_users"), 5)
        self.assertNotIn("r9", [row.get("id") for row in plan["reads"]])
        skipped_ids = [row.get("id") for row in plan["skipped"]]
        self.assertTrue(skipped_ids)
        self.assertTrue(all(row["tool"] == "get_posts_quoted_posts" for row in plan["skipped"]))

    def test_reply_pages_follow_reply_volume_and_slow_the_estimate(self):
        posts = []
        for index in range(5):
            posts.append(starter(
                f"p{index}",
                f"2026-11-16T{10 + index:02d}:00:00Z",
                likes=1,
                reposts=1 if index == 0 else 0,
                quotes=0,
                replies=15,
            ))
        plan = xn.trial_plan(posts, limit=5, budget=15)
        self.assertEqual(plan["reply_count"], 75)
        self.assertEqual(plan["reply_page_size"], 25)
        self.assertEqual(plan["reply_pages_needed"], 3)
        self.assertEqual(plan["reply_pages"], 3)
        search = [row for row in plan["reads"] if row["tool"] == "search_posts_all"]
        self.assertEqual(len(search), 1)
        self.assertEqual(search[0]["count"], 3)
        self.assertIn("25", search[0]["paging"])
        self.assertIn("first-level", search[0]["paging"])
        self.assertIn("Do not stop after one extra page", search[0]["paging"])
        self.assertEqual(plan["read_count"], 10)
        self.assertEqual(plan["estimate_minutes"], 3)
        self.assertIn("about 3 minutes", plan["estimate_message"])
        self.assertEqual(xn.estimate_minutes(10, sequential_seconds=180), 3)
        self.assertEqual(xn.estimate_minutes(15), 1)

    def test_reply_pages_stop_when_the_budget_ends(self):
        posts = [
            starter(
                f"p{index}",
                f"2026-11-16T{10 + index:02d}:00:00Z",
                likes=1,
                reposts=0,
                quotes=0,
                replies=100,
            )
            for index in range(5)
        ]
        plan = xn.trial_plan(posts, limit=5, budget=15)
        self.assertEqual(plan["reply_pages_needed"], 20)
        self.assertEqual(plan["reply_pages"], 14)
        self.assertEqual(plan["read_count"], 15)
        self.assertFalse(plan["within_budget"])
        tools = [row["tool"] for row in plan["reads"]]
        self.assertNotIn("get_posts_liking_users", tools)
        self.assertEqual(plan["estimate_minutes"], 14)

    def test_estimate_uses_reads_time_and_parallelism(self):
        self.assertEqual(xn.estimate_minutes(0), 0)
        self.assertEqual(xn.estimate_minutes(15), 1)
        self.assertEqual(xn.estimate_minutes(111), 4)
        self.assertEqual(xn.estimate_minutes(360), 12)
        self.assertEqual(xn.estimate_minutes(15, parallelism=100), 1)
        message = xn.estimate_message(15)
        self.assertIn("about 1 minute", message)
        self.assertIn("don't need to wait", message)
        self.assertIn("notify you when it's ready", message)


class StrangerMarkTest(unittest.TestCase):
    def _write(self, folder, with_following):
        me = "100"
        posts = {
            "data": [{
                "id": "7001",
                "author_id": me,
                "created_at": "2026-11-16T08:00:00Z",
                "text": "Window light.",
                "conversation_id": "7001",
                "public_metrics": {"like_count": 1},
            }],
            "includes": {"users": [{"id": me, "username": "demo_owner", "name": "Demo Owner"}]},
        }
        mentions = {
            "data": [{
                "id": "7101",
                "author_id": "201",
                "created_at": "2026-11-16T09:00:00Z",
                "text": "Saw the light.",
                "in_reply_to_user_id": me,
                "conversation_id": "7001",
                "referenced_tweets": [{"type": "replied_to", "id": "7001"}],
            }],
            "includes": {"users": [{"id": "201", "username": "ada_brook", "name": "Ada Brook"}]},
        }
        with open(os.path.join(folder, "posts_1.json"), "w", encoding="utf-8") as fh:
            json.dump(posts, fh)
        with open(os.path.join(folder, "mentions_1.json"), "w", encoding="utf-8") as fh:
            json.dump(mentions, fh)
        if with_following:
            with open(os.path.join(folder, "following_1.json"), "w", encoding="utf-8") as fh:
                json.dump({"data": []}, fh)
        return me

    def test_no_stranger_marks_without_a_following_file(self):
        folder = tempfile.mkdtemp(prefix="xn-nofollow-")
        me = self._write(folder, with_following=False)
        data = xn.Data(folder, me)
        self.assertFalse(data.following_known)
        since = xn.ts("2026-11-01T00:00:00Z")
        until = xn.ts(NOW)
        activity = xn.build_activity(
            data, since, until, 10, 1, 3, "both", until,
        )
        self.assertTrue(activity["interactions"])
        self.assertFalse(any(row["stranger"] for row in activity["interactions"]))
        path = os.path.join(folder, "report.md")
        xn.write_chat_report(activity, path, "demo_owner", since, until, "Last 7 days", window="week")
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertNotIn("👤?", report)
        self.assertNotIn("you don't follow", report)

    def test_marks_return_when_the_following_list_is_loaded(self):
        folder = tempfile.mkdtemp(prefix="xn-follow-")
        me = self._write(folder, with_following=True)
        data = xn.Data(folder, me)
        self.assertTrue(data.following_known)
        since = xn.ts("2026-11-01T00:00:00Z")
        until = xn.ts(NOW)
        activity = xn.build_activity(
            data, since, until, 10, 1, 3, "both", until,
        )
        self.assertTrue(any(row["stranger"] for row in activity["interactions"]))
        path = os.path.join(folder, "report.md")
        xn.write_chat_report(activity, path, "demo_owner", since, until, "Last 7 days", window="week")
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("👤?", report)


class TrialReportCommandTest(unittest.TestCase):
    def test_first_run_header_and_next_steps_skip_the_sixth_post(self):
        folder = tempfile.mkdtemp(prefix="xn-trial-")
        out = tempfile.mkdtemp(prefix="xn-trial-out-")
        me = "100"
        posts = []
        for index in range(1, 7):
            posts.append({
                "id": f"800{index}",
                "author_id": me,
                "created_at": f"2026-11-0{index}T08:00:00Z",
                "text": f"Post {index}",
                "conversation_id": f"800{index}",
                "public_metrics": {"like_count": 1, "repost_count": 0, "quote_count": 0},
            })
        posts.append({
            "id": "8901",
            "author_id": me,
            "created_at": "2026-11-16T11:00:00Z",
            "text": "Owner reply, not a trial post.",
            "conversation_id": "8006",
            "referenced_tweets": [{"type": "replied_to", "id": "1"}],
            "public_metrics": {"like_count": 8},
        })
        with open(os.path.join(folder, "posts_1.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "data": posts,
                "includes": {"users": [{"id": me, "username": "demo_owner", "name": "Demo"}]},
            }, fh)
        with open(os.path.join(folder, "likes_8001.json"), "w", encoding="utf-8") as fh:
            json.dump({"data": [{"id": "301", "username": "old_fan", "name": "Old Fan"}]}, fh)
        with open(os.path.join(folder, "likes_8006.json"), "w", encoding="utf-8") as fh:
            json.dump({"data": [{"id": "302", "username": "new_fan", "name": "New Fan"}]}, fh)
        cmd = [
            sys.executable, os.path.join(ROOT, "x_neighbours.py"),
            "--handle", "demo_owner",
            "--user-id", me,
            "--data", folder,
            "--out", out,
            "--now", "2026-11-16T12:00:00Z",
            "--first-run",
            "--no-picture",
            "--good-neighbours", os.path.join(ROOT, "examples", "few", "good_neighbours.json"),
            "--config", os.path.join(ROOT, "config.example.json"),
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["first"]
        self.assertEqual(payload["trial"]["post_count"], 5)
        self.assertNotIn("8001", payload["trial"]["posts"])
        self.assertIn("8006", payload["trial"]["posts"])
        self.assertEqual(
            payload["next_steps"]["options"],
            ["Daily report", "Weekly report", "Only when I ask"],
        )
        with open(payload["report_md"], encoding="utf-8") as fh:
            report = fh.read()
        self.assertTrue(report.startswith("Welcome tour"))
        self.assertNotIn("Old Fan", report)
        self.assertIn("New Fan", report)
        self.assertNotIn("👤?", report)
        self.assertNotIn("add_question", payload)
        self.assertNotIn("suggest_good_neighbours", payload)
        self.assertNotIn("good_neighbours_page", payload)
        self.assertFalse(payload["community_offer"])
        self.assertFalse(payload["share_offer"])
        self.assertTrue(payload["intro"].startswith("Here's how X Neighbours works."))
        intro_lines = [line for line in payload["intro"].splitlines() if line.strip()]
        self.assertGreaterEqual(len(intro_lines), 2)
        self.assertLessEqual(len(intro_lines), 3)
        self.assertIn("good neighbours", payload["intro"].lower())
        self.assertIn("neighbours picture", payload["intro"].lower())
        self.assertIn("community list", payload["intro"].lower())
        self.assertIn(payload["intro"], report)
        self.assertIn(xn.TOUR_FLOWERS, report)
        self.assertNotIn("Add to good neighbours", report)
        self.assertNotIn("Join the community", report)
        self.assertNotIn("share", payload["intro"].lower())
        with open(payload["thanks_md"], encoding="utf-8") as fh:
            thanks = fh.read()
        self.assertIn(xn.TOUR_THANKS, thanks)
        self.assertIn(xn.TOUR_NEXT_HEADING, thanks)
        self.assertIn("daily, weekly, or only when you ask", thanks)
        self.assertIn(xn.TOUR_NEXT_LATER, thanks)

        plan = subprocess.run(
            [sys.executable, os.path.join(ROOT, "x_neighbours.py"),
             "--trial-plan", "--data", folder, "--config", os.path.join(ROOT, "config.example.json")],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        planned = json.loads(plan.stdout)
        self.assertEqual(planned["trial_post_count"], 5)
        self.assertEqual(len(planned["posts"]), 5)
        self.assertNotIn("8901", [row["id"] for row in planned["posts"]])
        self.assertLessEqual(planned["read_count"], 15)

        estimate = subprocess.run(
            [sys.executable, os.path.join(ROOT, "x_neighbours.py"), "--estimate-reads", "15"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        estimated = json.loads(estimate.stdout)
        self.assertEqual(estimated["estimate_minutes"], 1)
        self.assertIn("don't need to wait", estimated["message"])


if __name__ == "__main__":
    unittest.main()
