#!/usr/bin/env python3
"""Run a week report from saved JSON, with no X calls."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "offline")
NOW = "2026-11-16T12:00:00Z"
NEIGHBOURS = [
    ("201", "waitalot", "Pat Wait"),
    ("202", "postsonly", "Quinn Posts"),
    ("203", "backforth", "Riley Back"),
    ("204", "neighbour4", "Four Glen"),
    ("205", "neighbour5", "Five Glen"),
    ("206", "neighbour6", "Six Glen"),
]


def write_latest(folder, uid, username, name):
    path = os.path.join(folder, f"latest_{uid}.json")
    if os.path.isfile(path):
        return
    doc = {
        "data": [{
            "id": f"82{uid}",
            "author_id": uid,
            "created_at": "2026-11-13T05:50:50.000Z",
            "text": f"A note from {name}.",
        }],
        "includes": {"users": [{"id": uid, "name": name, "username": username}]},
        "meta": {"result_count": 1},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


class ReferencedFieldsTest(unittest.TestCase):
    def test_prefers_referenced_tweets_then_posts(self):
        both = {
            "referenced_tweets": [{"type": "replied_to", "id": "1"}],
            "referenced_posts": [{"type": "quoted", "id": "2"}],
        }
        self.assertEqual(xn.referenced(both)[0]["id"], "1")
        posts_only = {"referenced_posts": [{"type": "replied_to", "id": "2001"}]}
        self.assertEqual(xn.referenced(posts_only)[0]["id"], "2001")
        self.assertEqual(xn.referenced({}), [])


class CatchRecordIsoTest(unittest.TestCase):
    def test_iso_string_created_at_does_not_crash(self):
        person = {
            "name": "Pat Wait",
            "username": "waitalot",
            "good": True,
            "stranger": True,
            "latest_url": "https://x.com/waitalot/status/82101",
            "latest": {
                "text": "Kept the window light on.",
                "created_at": "2026-11-13T05:50:50.000Z",
            },
        }
        row = xn.catch_record(person)
        self.assertEqual(row["created_at"], "2026-11-13T05:50:50Z")

    def test_datetime_created_at_still_works(self):
        when = xn.ts("2026-11-13T05:50:50.000Z")
        person = {
            "name": "Pat Wait",
            "username": "waitalot",
            "latest_url": "https://x.com/waitalot/status/82101",
            "latest": {"text": "Hi", "created_at": when},
        }
        row = xn.catch_record(person)
        self.assertEqual(row["created_at"], "2026-11-13T05:50:50Z")


class GreetingRowsTest(unittest.TestCase):
    def test_counts_once_per_person_when_latest_is_unreplied(self):
        when = xn.ts
        thread = {
            "name": "Pat Wait",
            "username": "waitalot",
            "items": [
                {"id": "3", "kind": "reply", "text": "third", "url": "u3",
                 "created_at": when("2026-11-13T08:00:00Z"), "replied": False,
                 "conversation_id": "2001", "on_id": "2001"},
                {"id": "2", "kind": "reply", "text": "second", "url": "u2",
                 "created_at": when("2026-11-13T07:00:00Z"), "replied": False,
                 "conversation_id": "2001", "on_id": "2001"},
                {"id": "1", "kind": "reply", "text": "first", "url": "u1",
                 "created_at": when("2026-11-13T06:00:00Z"), "replied": False,
                 "conversation_id": "2001", "on_id": "2001"},
            ],
        }
        unanswered, answered = xn.greeting_rows([thread])
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0]["username"], "waitalot")
        self.assertEqual(unanswered[0]["text"], "third")
        self.assertEqual(answered, [])

    def test_back_and_forth_is_not_waiting_when_latest_was_replied(self):
        when = xn.ts
        thread = {
            "name": "Riley Back",
            "username": "backforth",
            "items": [
                {"id": "3302", "kind": "reply", "text": "latest", "url": "u2",
                 "created_at": when("2026-11-13T10:00:00Z"), "replied": True,
                 "conversation_id": "2001", "on_id": "2001"},
                {"id": "3301", "kind": "reply", "text": "older", "url": "u1",
                 "created_at": when("2026-11-13T04:00:00Z"), "replied": False,
                 "conversation_id": "2001", "on_id": "2001"},
            ],
        }
        unanswered, answered = xn.greeting_rows([thread])
        self.assertEqual(unanswered, [])
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["text"], "latest")


class OfflineWeekReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="xn-offline-")
        data = os.path.join(cls.tmp, "data")
        shutil.copytree(FIXTURES, data)
        for uid, username, name in NEIGHBOURS:
            write_latest(data, uid, username, name)
        out = os.path.join(cls.tmp, "out")
        os.makedirs(out)
        with open(os.path.join(out, "open_items.csv"), "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(xn.OPEN_ITEM_FIELDS))
            writer.writeheader()
            writer.writerow({
                "kind": "follow_back",
                "handle": "followme",
                "reason": "old reason",
                "link": "https://x.com/followme",
                "status": "done",
            })
            writer.writerow({
                "kind": "first_reply",
                "handle": "waitalot",
                "reason": "old",
                "link": "https://x.com/waitalot/status/3103",
                "status": "dropped",
            })
        cmd = [
            sys.executable, os.path.join(ROOT, "x_neighbours.py"),
            "--config", os.path.join(data, "offline.json"),
            "--data", data,
            "--out", out,
            "--now", NOW,
            "--windows", "week",
            "--good-neighbours", os.path.join(data, "good_neighbours.json"),
            "--state", os.path.join(out, "state.json"),
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        cls.result = result
        if result.returncode != 0:
            raise AssertionError(
                "offline report failed:\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        cls.output = json.loads(result.stdout)["week"]
        cls.out = out
        with open(cls.output["report_md"], encoding="utf-8") as fh:
            cls.report = fh.read()
        with open(os.path.join(out, "open_items.csv"), newline="", encoding="utf-8") as fh:
            cls.open_items = list(csv.DictReader(fh))

    def test_no_crash_and_stdout_is_json(self):
        self.assertEqual(self.result.returncode, 0)
        self.assertIn("replies", self.output)

    def test_all_output_files_are_written(self):
        wanted = {
            "report md": self.output["report_md"],
            "thanks md": self.output["thanks_md"],
            "more json": self.output["more_json"],
            "picture png": self.output["picture"],
            "state json": os.path.join(self.out, "state.json"),
            "open items csv": os.path.join(self.out, "open_items.csv"),
        }
        missing = [name for name, path in wanted.items() if not os.path.isfile(path)]
        self.assertEqual(missing, [], f"missing files: {wanted}")
        self.assertGreater(os.path.getsize(self.output["picture"]), 0)
        self.assertEqual(self.output["messages"][0]["images"], [])
        self.assertEqual(self.output["fallback_messages"][1]["images"], [self.output["picture"]])
        self.assertTrue(self.output["picture_asset"].startswith("asset:"))
        with open(self.output["page_canvas"], encoding="utf-8") as fh:
            canvas = fh.read()
        self.assertIn(self.output["picture_asset"], canvas)
        self.assertIn("<Image", canvas)
        self.assertEqual(self.output["picture_nudge"], xn.PICTURE_NUDGE)
        self.assertEqual(self.output["good_neighbours_page"]["page_title"], "Good Neighbours Newsletter")

    def test_waiting_is_counted_once_per_person(self):
        waiting = [row for row in self.open_items if row["kind"] == "first_reply"]
        handles = sorted(row["handle"] for row in waiting)
        self.assertEqual(handles, ["postsonly", "waitalot"])
        self.assertEqual(self.report.count("Not replied yet"), 2)
        self.assertIn("This mention only has referenced_posts.", self.report)
        self.assertNotIn("First hello from the same person.", self.report)
        self.assertNotIn("Second hello from the same person.", self.report)
        self.assertIn("Third hello from the same person.", self.report)
        self.assertNotIn("Latest note; owner already replied.", self.report)
        self.assertIn("1 already answered.", self.report)

    def test_open_items_preserves_done_and_dropped(self):
        by_key = {(row["kind"], row["handle"]): row["status"] for row in self.open_items}
        self.assertEqual(by_key[("follow_back", "followme")], "done")
        self.assertEqual(by_key[("first_reply", "waitalot")], "dropped")
        self.assertEqual(by_key[("first_reply", "postsonly")], "open")
        self.assertEqual(by_key[("thank_you", "testowner")], "open")
        self.assertIn(("catch_up", "waitalot"), by_key)

    def test_more_json_has_iso_created_at_from_catch_record(self):
        with open(self.output["more_json"], encoding="utf-8") as fh:
            more = json.load(fh)
        extra = more.get("catch_up") or []
        self.assertTrue(extra, "need more than five catch-up people to hit catch_record")
        for row in extra:
            self.assertTrue(row.get("created_at"))
            self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class PictureOnlyTest(unittest.TestCase):
    def test_picture_only_skips_the_report_page(self):
        tmp = tempfile.mkdtemp(prefix="xn-picture-only-")
        data = os.path.join(tmp, "data")
        shutil.copytree(FIXTURES, data)
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        result = subprocess.run(
            [
                sys.executable, os.path.join(ROOT, "x_neighbours.py"),
                "--config", os.path.join(data, "offline.json"),
                "--data", data,
                "--out", out,
                "--now", NOW,
                "--picture-only",
                "--good-neighbours", os.path.join(data, "good_neighbours.json"),
            ],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(os.path.isfile(payload["picture"]))
        self.assertGreater(os.path.getsize(payload["picture"]), 0)
        self.assertEqual(payload["messages"][0]["images"], [payload["picture"]])
        self.assertNotIn("page_canvas", payload)
        self.assertIn("thank_you", payload)
        self.assertFalse(os.path.exists(os.path.join(out, "report_week.canvas.tsx")))


if __name__ == "__main__":
    unittest.main()
