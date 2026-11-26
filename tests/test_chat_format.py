#!/usr/bin/env python3
"""Chat report stays readable on a phone and still hides engaged posts."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISIBLE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")


def visible(line):
    text = VISIBLE_LINK.sub(r"\1", line)
    text = URL.sub("", text)
    return text.strip()


class ChatFormatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="xn-chat-")
        cmd = [
            sys.executable, os.path.join(ROOT, "x_neighbours.py"),
            "--config", os.path.join(ROOT, "config.example.json"),
            "--handle", "fewcreator",
            "--user-id", "800001",
            "--data", os.path.join(ROOT, "examples", "few"),
            "--good-neighbours", os.path.join(ROOT, "examples", "few", "good_neighbours.json"),
            "--out", cls.tmp,
            "--now", "2026-11-04T18:00:00Z",
            "--windows", "day",
            "--no-picture",
        ]
        result = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
        cls.output = json.loads(result.stdout)["day"]
        with open(cls.output["report_md"], encoding="utf-8") as fh:
            cls.report = fh.read()
        with open(cls.output["thanks_md"], encoding="utf-8") as fh:
            cls.thanks = fh.read()
        with open(cls.output["more_json"], encoding="utf-8") as fh:
            cls.more = json.load(fh)

    def test_section_order_and_no_table(self):
        flowers = self.report.index("### 💐 Flowers")
        catch = self.report.index("### 🏡 What's new")
        greet = self.report.index("### 👋 Replies")
        self.assertLess(flowers, catch)
        self.assertLess(catch, greet)
        self.assertNotIn("|---", self.report)
        self.assertNotIn("| User |", self.report)
        self.assertNotIn("![", self.report)
        self.assertNotIn("<img", self.report)

    def test_header_is_two_lines_without_a_same_day_range(self):
        head = self.report.split("### ")[0]
        raw = [line for line in head.splitlines() if line.strip()]
        lines = [line.rstrip() for line in raw]
        self.assertEqual(lines[0], "Last 24 hours")
        self.assertEqual(lines[1], "💬 2 · 🔁💬 1 · ❤️ 6 · 🔁 1")
        self.assertTrue(raw[0].endswith("  "))
        self.assertTrue(raw[1].endswith("  "))
        self.assertNotIn("reply", lines[1].lower())
        self.assertNotRegex(self.report, r"Nov \d+.Nov \d+")

    def test_suggestion_labels_use_name_and_handle(self):
        options = self.output["add_question"]["options"]
        self.assertTrue(options)
        for label in options:
            self.assertRegex(label, r"^[^@]+ \(@[A-Za-z0-9_]+\)$")
            self.assertNotRegex(label, r"^⭐ @")
        self.assertIn("Cleo Dawn (@cleo_dawn)", options)

    def test_avatar_metadata_is_kept_out_of_the_chat_text(self):
        self.assertTrue(any(row.get("avatar") for row in self.more["flowers"]))
        answered = self.more["greetings_answered"]
        self.assertTrue(answered)
        self.assertIn("avatar", answered[0])

    def test_flowers_are_stacked_and_capped(self):
        block = self.report.split("### 🏡")[0]
        self.assertNotIn("@ada_brook", block)
        self.assertIn("[Ada Brook](https://x.com/ada_brook)", block)
        self.assertIn("1 reply · 1 like", block)
        self.assertIn("more in Flowers. Ask to see them.", block)
        self.assertGreaterEqual(len(self.more["flowers"]), 1)
        self.assertLessEqual(block.count("https://x.com/"), 5)

    def test_catch_up_preview_has_one_link(self):
        block = self.report.split("### 🏡 What's new")[1].split("### ")[0]
        self.assertIn("Kept the window light on.", block)
        self.assertNotIn("Quoting my own note", block)
        self.assertNotIn("Read post", block)
        self.assertNotIn("@ada_brook", block)
        self.assertNotIn("@fay_glen", block)
        self.assertIn("](https://x.com/ada_brook/status/83101)", block)
        self.assertNotIn("more to catch up", block)
        self.assertEqual(self.more["catch_up"], [])

    def test_greeting_preview_links_to_the_post(self):
        block = self.report.split("### 👋 Replies")[1]
        self.assertRegex(block, r"\]\(https://x\.com/[A-Za-z0-9_]+/status/\w+\)")
        self.assertIn("Not replied yet", block)
        for row in self.more["greetings_answered"]:
            self.assertTrue(row["replied"])
            self.assertEqual(row["status"], "Replied")
            self.assertIn(f"]({row['url']})", row["markdown"])
            self.assertIn("· Replied", row["markdown"])
            self.assertNotIn("Not replied yet", row["markdown"])

    def test_answered_greeting_lines_say_replied(self):
        now = xn.ts("2026-11-04T18:00:00Z")
        row = {
            "name": "Ada Brook",
            "username": "ada_brook",
            "kind": "reply",
            "text": "I could see it from the corner.",
            "url": "https://x.com/ada_brook/status/71001",
            "on_text": "Left a light on in the window.",
            "created_at": "2026-11-04T10:00:00Z",
            "replied": True,
        }
        text = "\n".join(xn.greeting_lines(row, now))
        self.assertIn("[“I could see it from the corner.”](https://x.com/ada_brook/status/71001)", text)
        self.assertIn("· Replied", text)
        self.assertNotIn("Not replied yet", text)
        waiting = dict(row, replied=False)
        waiting_text = "\n".join(xn.greeting_lines(waiting, now))
        self.assertIn("· Not replied yet", waiting_text)
        self.assertNotIn("· Replied", waiting_text)

    def test_markdown_hard_breaks_separate_name_and_detail(self):
        self.assertRegex(
            self.report,
            r"\*\*\[Ada Brook\]\(https://x.com/ada_brook\)\*\* ⭐  \n1 reply · 1 like",
        )
        self.assertRegex(
            self.report,
            r"\*\*Ada Brook\*\* ⭐  \n\[“Kept the window light on\.”\]",
        )
        greet = self.report.split("### 👋 Replies")[1]
        self.assertIn("  \n", greet)

    def test_greetings_skip_answered_by_default(self):
        block = self.report.split("### 👋 Replies")[1]
        self.assertIn("Not replied yet", block)
        self.assertNotIn("already replied", block.lower())
        self.assertNotRegex(block, r"^\s+- ", )
        self.assertIn("already answered. Ask to see them.", block)
        self.assertTrue(any(row["replied"] for row in self.more["greetings_answered"]))
        self.assertNotIn("    - ", block)

    def test_lines_wrap_without_a_long_unbroken_token(self):
        for line in self.report.splitlines():
            shown = visible(line)
            for token in shown.split():
                self.assertLessEqual(len(token), 40, token)

    def test_thanks_note_does_not_claim_the_link_attaches_the_picture(self):
        self.assertIn("Save the picture and attach it in X before posting.", self.thanks)
        self.assertIn(self.thanks.splitlines()[0], self.output["thank_you"]["text"])
        self.assertNotIn("@", self.output["thank_you"]["text"])
        self.assertEqual(
            self.output["thank_you"]["note"],
            "Save the picture and attach it in X before posting.",
        )
        self.assertNotIn("###", self.thanks)


class PeriodLabelTest(unittest.TestCase):
    def test_elapsed_is_minutes_and_hours_not_a_calendar(self):
        start = xn.ts("2026-11-04T17:30:00Z")
        end = xn.ts("2026-11-04T18:00:00Z")
        self.assertEqual(xn.elapsed_phrase(start, end), "30 min ago")
        self.assertEqual(
            xn.period_line("fresh", start, end, "Since your last report"),
            "Since your last report · 30 min ago",
        )
        later = xn.ts("2026-11-04T19:00:00Z")
        self.assertEqual(xn.elapsed_phrase(end, later), "1 hr ago")
        self.assertEqual(xn.period_line("day", start, end, "last 24 hours"), "Last 24 hours")
        self.assertEqual(xn.period_line("week", start, end, "last 7 days"), "Last 7 days")
        self.assertEqual(
            xn.period_line("first", start, end, "Last 2 days"),
            "Welcome tour",
        )

    def test_timezone_does_not_change_the_elapsed_interval(self):
        start = xn.ts("2026-11-04T17:30:00Z")
        end = xn.ts("2026-11-04T18:00:00Z")
        zone = xn.owner_zone("Pacific/Kiritimati")
        shifted = end.astimezone(zone)
        self.assertEqual(xn.elapsed_phrase(start, shifted), "30 min ago")
        self.assertNotIn("Nov", xn.period_line("fresh", start, shifted, ""))


class WaitingConversationTest(unittest.TestCase):
    def _thread(self, uid, name, username, items):
        return {
            "id": uid,
            "name": name,
            "username": username,
            "items": items,
        }

    def _item(self, pid, when, conv, on_id, replied=False):
        return {
            "id": pid,
            "kind": "reply",
            "text": f"note {pid}",
            "url": f"https://x.com/ada_brook/status/{pid}",
            "on_id": on_id,
            "on_text": "your post",
            "created_at": xn.ts(when),
            "conversation_id": conv,
            "replied": replied,
        }

    def test_follow_up_in_answered_thread_is_not_waiting(self):
        thread = self._thread("800101", "Ada Brook", "ada_brook", [
            self._item("71001", "2026-11-04T10:00:00Z", "70001", "70001", replied=True),
            self._item("71003", "2026-11-04T12:00:00Z", "70001", "89001", replied=False),
        ])
        unanswered, answered = xn.greeting_rows([thread], {("70001", "800101")})
        self.assertEqual(unanswered, [])
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["url"], "https://x.com/ada_brook/status/71003")
        self.assertTrue(answered[0]["replied"])

    def test_waiting_is_once_per_person_per_conversation(self):
        thread = self._thread("800103", "Cleo Dawn", "cleo_dawn", [
            self._item("71002", "2026-11-04T11:30:00Z", "70002", "70002"),
            self._item("71004", "2026-11-04T13:00:00Z", "70002", "70002"),
        ])
        unanswered, answered = xn.greeting_rows([thread], set())
        self.assertEqual(answered, [])
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0]["url"], "https://x.com/ada_brook/status/71004")
        self.assertFalse(unanswered[0]["replied"])

    def test_two_conversations_stay_separate(self):
        thread = self._thread("800101", "Ada Brook", "ada_brook", [
            self._item("71001", "2026-11-04T10:00:00Z", "70001", "70001", replied=True),
            self._item("71005", "2026-11-04T11:00:00Z", "70002", "70002", replied=False),
        ])
        unanswered, answered = xn.greeting_rows([thread], {("70001", "800101")})
        self.assertEqual(len(answered), 1)
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0]["url"], "https://x.com/ada_brook/status/71005")

    def test_owner_reply_in_conversation_marks_mentions_answered(self):
        folder = tempfile.mkdtemp(prefix="xn-wait-")
        with open(os.path.join(folder, "posts_1.json"), "w", encoding="utf-8") as fh:
            json.dump({"data": [
                {"id": "1", "author_id": "me", "created_at": "2026-11-04T09:00:00Z", "text": "hi"},
                {"id": "3", "author_id": "me", "created_at": "2026-11-04T11:00:00Z",
                 "text": "thanks",
                 "referenced_tweets": [{"type": "replied_to", "id": "2"}]},
            ]}, fh)
        with open(os.path.join(folder, "mentions_1.json"), "w", encoding="utf-8") as fh:
            json.dump({"data": [
                {"id": "2", "author_id": "u1", "created_at": "2026-11-04T10:00:00Z",
                 "text": "first", "in_reply_to_user_id": "me",
                 "referenced_tweets": [{"type": "replied_to", "id": "1"}],
                 "conversation_id": "1"},
                {"id": "4", "author_id": "u1", "created_at": "2026-11-04T12:00:00Z",
                 "text": "follow-up", "in_reply_to_user_id": "me",
                 "referenced_tweets": [{"type": "replied_to", "id": "3"}],
                 "conversation_id": "1"},
            ], "includes": {"users": [{"id": "u1", "username": "ada", "name": "Ada"}]}}, fh)
        data = xn.Data(folder, "me")
        self.assertIn(("1", "u1"), data.owner_replied_in)
        now = xn.ts("2026-11-04T18:00:00Z")
        activity = xn.build_activity(
            data, xn.ts("2026-11-04T00:00:00Z"), now, 10, 1, 3, "both", now)
        unanswered, answered = xn.greeting_rows(
            activity["threads"], activity["owner_replied_in"])
        self.assertEqual(unanswered, [])
        self.assertEqual(len(answered), 1)
        self.assertTrue(answered[0]["replied"])


class CatchRecordTest(unittest.TestCase):
    def test_string_created_at_is_parsed(self):
        row = xn.catch_record({
            "name": "Nova Lane",
            "username": "nova_lane",
            "latest": {
                "text": "A paper street with no cars, only chairs.",
                "created_at": "2026-11-13T05:50:50.000Z",
            },
            "latest_url": "https://x.com/nova_lane/status/83001",
        })
        self.assertEqual(row["created_at"], "2026-11-13T05:50:50Z")

    def test_datetime_created_at_still_formats(self):
        when = xn.ts("2026-11-04T16:30:00Z")
        row = xn.catch_record({
            "name": "Nova Lane",
            "username": "nova_lane",
            "latest": {"text": "A paper street.", "created_at": when},
            "latest_url": "https://x.com/nova_lane/status/83001",
        })
        self.assertEqual(row["created_at"], "2026-11-04T16:30:00Z")

    def test_week_sample_writes_thanks_and_more_json(self):
        tmp = tempfile.mkdtemp(prefix="xn-week-")
        cmd = [
            sys.executable, os.path.join(ROOT, "x_neighbours.py"),
            "--config", os.path.join(ROOT, "config.example.json"),
            "--handle", "samplecreator",
            "--user-id", "900001",
            "--data", os.path.join(ROOT, "examples", "sample"),
            "--out", tmp,
            "--now", "2026-11-04T18:00:00Z",
            "--no-picture",
        ]
        result = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
        output = json.loads(result.stdout)["week"]
        self.assertTrue(os.path.isfile(output["thanks_md"]))
        self.assertTrue(os.path.isfile(output["more_json"]))
        with open(output["more_json"], encoding="utf-8") as fh:
            more = json.load(fh)
        self.assertGreater(len(more["catch_up"]), 0)
        for row in more["catch_up"]:
            self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class GreetingRowsTest(unittest.TestCase):
    def test_counts_once_per_person_when_latest_is_unreplied(self):
        thread = {
            "name": "Pat Wait",
            "username": "waitalot",
            "items": [
                {"id": "3", "kind": "reply", "text": "third", "url": "u3",
                 "created_at": xn.ts("2026-11-13T08:00:00Z"), "replied": False},
                {"id": "2", "kind": "reply", "text": "second", "url": "u2",
                 "created_at": xn.ts("2026-11-13T07:00:00Z"), "replied": False},
                {"id": "1", "kind": "reply", "text": "first", "url": "u1",
                 "created_at": xn.ts("2026-11-13T06:00:00Z"), "replied": False},
            ],
        }
        unanswered, answered = xn.greeting_rows([thread])
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0]["username"], "waitalot")
        self.assertEqual(unanswered[0]["text"], "third")
        self.assertEqual(answered, [])

    def test_back_and_forth_is_not_waiting_when_latest_was_replied(self):
        thread = {
            "name": "Riley Back",
            "username": "backforth",
            "items": [
                {"id": "3302", "kind": "reply", "text": "latest", "url": "u2",
                 "created_at": xn.ts("2026-11-13T10:00:00Z"), "replied": True},
                {"id": "3301", "kind": "reply", "text": "older", "url": "u1",
                 "created_at": xn.ts("2026-11-13T04:00:00Z"), "replied": False},
            ],
        }
        unanswered, answered = xn.greeting_rows([thread])
        self.assertEqual(unanswered, [])
        self.assertEqual(len(answered), 1)
        self.assertEqual(answered[0]["text"], "latest")


if __name__ == "__main__":
    unittest.main()
