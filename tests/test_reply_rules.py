#!/usr/bin/env python3
"""First-level replies, and no liker fetch for the owner's replies.

Handles, names, and ids here are fictional. Nothing in this file is a real account.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rules
import share_submission as share
import x_neighbours as xn

ME = "100"
NOW = "2026-11-16T12:00:00Z"


def dump(folder, name, doc):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def user(uid, username, name):
    return {"id": uid, "username": username, "name": name}


class LikerSkipTest(unittest.TestCase):
    def test_replies_are_never_fetched_originals_and_quotes_keep_the_budget(self):
        now = NOW
        reply = {
            "id": "5002",
            "created_at": "2026-11-16T10:00:00Z",
            "conversation_id": "5001",
            "referenced_tweets": [{"type": "replied_to", "id": "6001"}],
            "public_metrics": {"like_count": 9},
        }
        original = {
            "id": "5001",
            "created_at": "2026-11-16T08:00:00Z",
            "conversation_id": "5001",
            "public_metrics": {"like_count": 1},
        }
        quote = {
            "id": "5003",
            "created_at": "2026-11-16T09:00:00Z",
            "conversation_id": "999",
            "referenced_tweets": [{"type": "quoted", "id": "999"}],
            "public_metrics": {"like_count": 1},
        }
        quote_reply = {
            "id": "5009",
            "created_at": "2026-11-16T09:30:00Z",
            "conversation_id": "5001",
            "in_reply_to_user_id": ME,
            "referenced_tweets": [
                {"type": "replied_to", "id": "5001"},
                {"type": "quoted", "id": "999"},
            ],
            "public_metrics": {"like_count": 4},
        }
        self.assertFalse(xn.should_fetch_likers(reply, now))
        self.assertFalse(xn.should_fetch_likers(quote_reply, now))
        self.assertTrue(xn.should_fetch_likers(original, now))
        self.assertTrue(xn.is_owner_original_post(quote))
        self.assertTrue(xn.should_fetch_likers(quote, now))
        older = dict(original, created_at="2026-11-13T12:00:00Z",
                     public_metrics={"like_count": 2})
        self.assertFalse(xn.should_fetch_likers(older, now))
        older["public_metrics"] = {"like_count": 3}
        self.assertTrue(xn.should_fetch_likers(older, now))
        week = dict(original, created_at="2026-11-09T11:59:59Z",
                    public_metrics={"like_count": 10})
        self.assertFalse(xn.should_fetch_likers(week, now))
        edge = dict(original, created_at="2026-11-15T12:00:00Z",
                    public_metrics={"like_count": 1})
        self.assertTrue(xn.should_fetch_likers(edge, now))


class FirstLevelReplyTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="xn-level-")
        posts = {
            "data": [
                {
                    "id": "5001",
                    "author_id": ME,
                    "created_at": "2026-11-16T08:00:00Z",
                    "text": "P, the original.",
                    "conversation_id": "5001",
                    "public_metrics": {"like_count": 4, "repost_count": 0, "quote_count": 0},
                },
                {
                    "id": "5002",
                    "author_id": ME,
                    "created_at": "2026-11-16T10:00:00Z",
                    "text": "Owner answer in the thread.",
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "6001"}],
                    "public_metrics": {"like_count": 9, "repost_count": 0, "quote_count": 0},
                },
                {
                    "id": "5003",
                    "author_id": ME,
                    "created_at": "2026-11-16T09:00:00Z",
                    "text": "A quote that starts its own conversation.",
                    "conversation_id": "999",
                    "referenced_tweets": [{"type": "quoted", "id": "999"}],
                    "public_metrics": {"like_count": 1, "repost_count": 0, "quote_count": 0},
                },
                {
                    "id": "5004",
                    "author_id": ME,
                    "created_at": "2026-11-16T11:00:00Z",
                    "text": "Another owner reply, likers not fetched.",
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "6001"}],
                    "public_metrics": {"like_count": 5, "repost_count": 0, "quote_count": 0},
                },
            ],
            "includes": {"users": [user(ME, "demo_owner", "Demo Owner")]},
        }
        mentions = {
            "data": [
                {
                    "id": "6001",
                    "author_id": "201",
                    "created_at": "2026-11-16T09:00:00Z",
                    "text": "A1, the first reply to P.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "5001"}],
                },
                {
                    "id": "6002",
                    "author_id": "201",
                    "created_at": "2026-11-16T11:30:00Z",
                    "text": "A2, later reply under the owner.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "5002"}],
                },
                {
                    "id": "6003",
                    "author_id": "201",
                    "created_at": "2026-11-16T11:45:00Z",
                    "text": "Only conversation id and user id.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5001",
                },
                {
                    "id": "6004",
                    "author_id": "202",
                    "created_at": "2026-11-16T09:30:00Z",
                    "text": "Reply to the quote.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5003",
                    "referenced_tweets": [{"type": "replied_to", "id": "5003"}],
                },
                {
                    "id": "6005",
                    "author_id": "203",
                    "created_at": "2026-11-16T08:30:00Z",
                    "text": "First note from Bea.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "5001"}],
                },
                {
                    "id": "6006",
                    "author_id": "203",
                    "created_at": "2026-11-16T08:40:00Z",
                    "text": "Second note from Bea.",
                    "in_reply_to_user_id": ME,
                    "conversation_id": "5001",
                    "referenced_tweets": [{"type": "replied_to", "id": "5001"}],
                },
            ],
            "includes": {
                "users": [
                    user("201", "ada_brook", "Ada Brook"),
                    user("202", "hal_keel", "Hal Keel"),
                    user("203", "bea_coast", "Bea Coast"),
                    user("204", "cam_dale", "Cam Dale"),
                    user("205", "dee_elm", "Dee Elm"),
                ],
            },
        }
        dump(self.folder, "posts_1.json", posts)
        dump(self.folder, "mentions_1.json", mentions)
        dump(self.folder, "likes_5001.json", {"data": [user("203", "bea_coast", "Bea Coast"),
                                                       user("204", "cam_dale", "Cam Dale")]})
        dump(self.folder, "likes_5003.json", {"data": [user("203", "bea_coast", "Bea Coast")]})
        dump(self.folder, "likes_5002.json", {"data": [user("205", "dee_elm", "Dee Elm")]})
        self.data = xn.Data(self.folder, ME)
        since = xn.ts("2026-11-09T12:00:00Z")
        until = xn.ts(NOW)
        self.activity = xn.build_activity(
            self.data, since, until, top=10, like_weight=1, reply_weight=3,
            mode="both", now=until,
        )

    def test_only_a1_counts_for_ada_and_a2_is_not_unanswered(self):
        self.assertIn("5001", self.data.owner_original_ids)
        self.assertIn("5003", self.data.owner_original_ids)
        self.assertNotIn("5002", self.data.owner_original_ids)
        by_id = {row["user_id"]: row for row in self.activity["share_counts"]}
        self.assertEqual(by_id["201"]["replies"], 1)
        self.assertEqual(by_id["201"]["likes"], 0)
        self.assertEqual(by_id["202"]["replies"], 1)
        self.assertEqual(by_id["203"]["replies"], 2)
        self.assertEqual(by_id["203"]["likes"], 2)
        # A saved liker file still scores, including one on an owner reply.
        # The fetch rule skips that call. The file, once saved, is counted.
        self.assertEqual(by_id["205"]["likes"], 1)
        self.assertEqual(
            rules.interaction_score(0, 0, by_id["201"]["replies"], 0),
            3,
        )
        ada_ok, ada_reason = rules.eligibility(
            {key: by_id["201"][key] for key in ("replies", "quotes", "reposts", "likes")},
            7,
        )
        self.assertFalse(ada_ok)
        self.assertEqual(ada_reason, "conversation")
        bea_ok, bea_reason = rules.eligibility(
            {key: by_id["203"][key] for key in ("replies", "quotes", "reposts", "likes")},
            7,
        )
        self.assertTrue(bea_ok, bea_reason)
        path = os.path.join(self.folder, "report.md")
        with patch.object(xn, "utc_now", return_value=xn.ts(NOW)):
            more = xn.write_chat_report(
                self.activity, path, "demo_owner",
                xn.ts("2026-11-09T12:00:00Z"), xn.ts(NOW), "Last 7 days",
                window="week",
            )
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("Reply to the quote.", report)
        self.assertNotIn("A2, later reply under the owner.", report)
        self.assertNotIn("Only conversation id and user id.", report)
        self.assertNotIn("Owner answer in the thread.", report)
        answered = [row["username"] for row in more["greetings_answered"]]
        unanswered = [row["text"] for row in more["greetings_unanswered"]]
        self.assertIn("ada_brook", answered)
        self.assertNotIn("A2, later reply under the owner.", unanswered)
        self.assertIn("1 reply", report)
        self.assertNotIn("2 replies", report.split("Bea Coast")[0])

    def test_share_counts_ignore_reply_likers_when_the_file_is_missing_or_present(self):
        rows = share.counts_from_data(self.folder, ME, NOW, 7)
        by_id = {row["user_id"]: row for row in rows}
        self.assertEqual(by_id["201"]["replies"], 1)
        self.assertEqual(by_id["205"]["likes"], 1)
        self.assertEqual(self.activity["totals"]["likes"], 4)


if __name__ == "__main__":
    unittest.main()
