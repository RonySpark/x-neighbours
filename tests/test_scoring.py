#!/usr/bin/env python3
"""Optional scoring weights keep the old defaults and can boost mutuals."""
import json
import os
import tempfile
import unittest

import x_neighbours as xn


def dump(folder, name, doc):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def sample_data():
    folder = tempfile.mkdtemp(prefix="xn-score-")
    dump(folder, "posts_1.json", {"data": [
        {"id": "1", "author_id": "me", "created_at": "2026-11-04T09:00:00Z",
         "text": "an original post"},
        {"id": "9", "author_id": "me", "created_at": "2026-11-04T09:30:00Z",
         "text": "a reply of mine",
         "in_reply_to_user_id": "u3",
         "referenced_tweets": [{"type": "replied_to", "id": "8"}]},
    ]})
    dump(folder, "mentions_1.json", {"data": [
        {"id": "2", "author_id": "u1", "created_at": "2026-11-04T10:00:00Z",
         "text": "mutual on original", "in_reply_to_user_id": "me",
         "referenced_tweets": [{"type": "replied_to", "id": "1"}],
         "conversation_id": "1"},
        {"id": "3", "author_id": "u2", "created_at": "2026-11-04T10:05:00Z",
         "text": "new follower on original", "in_reply_to_user_id": "me",
         "referenced_tweets": [{"type": "replied_to", "id": "1"}],
         "conversation_id": "1"},
        {"id": "4", "author_id": "u1", "created_at": "2026-11-04T10:10:00Z",
         "text": "mutual on owner reply", "in_reply_to_user_id": "me",
         "referenced_tweets": [{"type": "replied_to", "id": "9"}],
         "conversation_id": "8"},
    ], "includes": {"users": [
        {"id": "u1", "username": "ada", "name": "Ada",
         "connection_status": ["following", "followed_by"]},
        {"id": "u2", "username": "ben", "name": "Ben",
         "connection_status": ["followed_by"]},
    ]}})
    dump(folder, "likes_1.json", {"data": [
        {"id": "u1", "username": "ada", "name": "Ada"},
        {"id": "u2", "username": "ben", "name": "Ben"},
    ]})
    dump(folder, "reposts_1.json", {"data": [
        {"id": "u2", "username": "ben", "name": "Ben"},
    ]})
    dump(folder, "quotes_1.json", {"data": [{
        "id": "5", "author_id": "u2", "created_at": "2026-11-04T11:00:00Z",
        "text": "quoting",
        "referenced_tweets": [{"type": "quoted", "id": "1"}],
        "conversation_id": "1",
    }], "includes": {"users": [
        {"id": "u2", "username": "ben", "name": "Ben",
         "connection_status": ["followed_by"]},
    ]}})
    dump(folder, "followers_1.json", {"data": [
        {"id": "u1", "username": "ada"},
        {"id": "u2", "username": "ben"},
    ]})
    dump(folder, "following_1.json", {"data": [
        {"id": "u1", "username": "ada"},
    ]})
    return xn.Data(folder, "me")


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.data = sample_data()
        self.since = xn.ts("2026-11-04T00:00:00Z")
        self.now = xn.ts("2026-11-04T18:00:00Z")

    def ranked(self, **weights):
        activity = xn.build_activity(
            self.data, self.since, self.now, 10, 1, 3, "both", self.now, **weights)
        return activity["ranked"]

    def test_defaults_match_old_like_and_reply_weights(self):
        ranked = self.ranked()
        # u1: 1 like * 1 + 2 replies * 3 = 7
        # u2: 1 like * 1 + 1 repost * 1 + 1 reply * 3 + 1 quote * 3 = 8
        self.assertEqual(ranked["u1"], 7)
        self.assertEqual(ranked["u2"], 8)

    def test_omitted_repost_and_quote_weights_inherit(self):
        cfg = xn.resolve_weights({"like_weight": 2, "reply_weight": 5})
        self.assertEqual(cfg["repost_weight"], 2)
        self.assertEqual(cfg["quote_weight"], 5)
        self.assertEqual(cfg["mutual_reply_bonus"], 0)
        self.assertEqual(cfg["new_follower_bonus"], 0)

    def test_explicit_repost_and_quote_weights(self):
        ranked = self.ranked(repost_weight=4, quote_weight=7)
        # u2: 1 like * 1 + 1 repost * 4 + 1 reply * 3 + 1 quote * 7 = 15
        self.assertEqual(ranked["u2"], 15)
        self.assertEqual(ranked["u1"], 7)

    def test_mutual_reply_bonus_only_on_original_posts(self):
        ranked = self.ranked(mutual_reply_bonus=10)
        # u1: old 7 + 1 original-post reply * 10 (the reply to post 9 is not original)
        self.assertEqual(ranked["u1"], 17)
        # u2 is follows-you, not mutual, so no mutual bonus. still 8
        self.assertEqual(ranked["u2"], 8)

    def test_new_follower_bonus_once_for_follows_you(self):
        ranked = self.ranked(new_follower_bonus=5)
        self.assertEqual(ranked["u1"], 7)
        self.assertEqual(ranked["u2"], 13)

    def test_connection_status_marks_mutual_without_lists(self):
        folder = tempfile.mkdtemp(prefix="xn-mutual-")
        dump(folder, "posts_1.json", {"data": [
            {"id": "1", "author_id": "me", "created_at": "2026-11-04T09:00:00Z", "text": "hi"},
        ]})
        dump(folder, "mentions_1.json", {"data": [{
            "id": "2", "author_id": "u9", "created_at": "2026-11-04T10:00:00Z",
            "text": "hello", "in_reply_to_user_id": "me",
            "referenced_tweets": [{"type": "replied_to", "id": "1"}],
            "conversation_id": "1",
        }], "includes": {"users": [{
            "id": "u9", "username": "zed", "name": "Zed",
            "connection_status": ["following", "followed_by"],
        }]}})
        data = xn.Data(folder, "me")
        self.assertEqual(data.relation("u9"), "mutual")
        activity = xn.build_activity(
            data, self.since, self.now, 10, 1, 3, "both", self.now,
            mutual_reply_bonus=4)
        self.assertEqual(activity["ranked"]["u9"], 7)


if __name__ == "__main__":
    unittest.main()
