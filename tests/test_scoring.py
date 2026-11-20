#!/usr/bin/env python3
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr

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
        {"id": "6", "author_id": "u1", "created_at": "2026-11-04T10:20:00Z",
         "text": "second first-level reply", "in_reply_to_user_id": "me",
         "referenced_tweets": [{"type": "replied_to", "id": "1"}],
         "conversation_id": "1"},
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
        note = getattr(xn, "note_missing_follower_baseline", None)
        if note is not None:
            note.done = False

    def ranked(self, **weights):
        activity = xn.build_activity(
            self.data, self.since, self.now, 10, 1, 3, "both", self.now, **weights)
        return activity["ranked"]

    def test_defaults_match_old_like_and_reply_weights(self):
        ranked = self.ranked()
        # u1: 1 like * 1 + 2 first-level replies * 3 = 7.
        # The reply under the owner's own reply does not count.
        # u2: 1 like * 1 + 1 repost * 1 + 1 reply * 3 + 1 quote * 3 = 8
        self.assertEqual(ranked["u1"], 7)
        self.assertEqual(ranked["u2"], 8)

    def test_omitted_repost_and_quote_weights_inherit(self):
        cfg = xn.resolve_weights({"like_weight": 2, "reply_weight": 5})
        self.assertEqual(cfg["repost_weight"], 2)
        self.assertEqual(cfg["quote_weight"], 5)
        self.assertEqual(cfg["mutual_bonus"], 0)
        self.assertEqual(cfg["new_follower_credit"], 0)
        self.assertNotIn("mutual_reply_bonus", cfg)
        self.assertNotIn("new_follower_bonus", cfg)

    def test_old_only_keys_map_to_kept(self):
        with redirect_stderr(io.StringIO()) as err:
            cfg = xn.resolve_weights({
                "mutual_reply_bonus": 2,
                "new_follower_bonus": 3,
            })
        self.assertEqual(cfg["mutual_bonus"], 2)
        self.assertEqual(cfg["new_follower_credit"], 3)
        text = err.getvalue()
        self.assertIn("mutual_reply_bonus", text)
        self.assertIn("mutual_bonus", text)
        self.assertIn("new_follower_bonus", text)
        self.assertIn("new_follower_credit", text)

    def test_kept_key_wins_when_both_set(self):
        with redirect_stderr(io.StringIO()) as err:
            cfg = xn.resolve_weights({
                "mutual_bonus": 5,
                "new_follower_credit": 4,
                "mutual_reply_bonus": 99,
                "new_follower_bonus": 99,
            })
        self.assertEqual(cfg["mutual_bonus"], 5)
        self.assertEqual(cfg["new_follower_credit"], 4)
        text = err.getvalue()
        self.assertIn("mutual_reply_bonus", text)
        self.assertIn("mutual_bonus", text)
        self.assertIn("new_follower_bonus", text)
        self.assertIn("new_follower_credit", text)

    def test_defaults_do_not_hide_old_only_file_keys(self):
        loaded = {"mutual_reply_bonus": 2, "new_follower_bonus": 3}
        merged = xn.apply_loaded_config(dict(xn.DEFAULTS), loaded)
        with redirect_stderr(io.StringIO()):
            cfg = xn.resolve_weights(merged)
        self.assertEqual(cfg["mutual_bonus"], 2)
        self.assertEqual(cfg["new_follower_credit"], 3)

    def test_explicit_repost_and_quote_weights(self):
        ranked = self.ranked(repost_weight=4, quote_weight=7)
        # u2: 1 like * 1 + 1 repost * 4 + 1 reply * 3 + 1 quote * 7 = 15
        self.assertEqual(ranked["u2"], 15)
        self.assertEqual(ranked["u1"], 7)

    def test_mutual_bonus_for_each_first_level_reply(self):
        ranked = self.ranked(mutual_bonus=10)
        # u1: activity 7, plus 10 for each of the two replies on the original post.
        # The reply under the owner's own reply is not in that 7 and adds no bonus.
        self.assertEqual(ranked["u1"], 27)
        # u2 is follows-you, not mutual
        self.assertEqual(ranked["u2"], 8)

    def test_mutual_bonus_for_each_reply_on_an_original(self):
        folder = tempfile.mkdtemp(prefix="xn-mutual-replies-")
        dump(folder, "posts_1.json", {"data": [
            {"id": "1", "author_id": "me", "created_at": "2026-11-04T09:00:00Z",
             "text": "first original"},
            {"id": "2", "author_id": "me", "created_at": "2026-11-04T09:10:00Z",
             "text": "second original"},
            {"id": "9", "author_id": "me", "created_at": "2026-11-04T09:30:00Z",
             "text": "a reply of mine", "in_reply_to_user_id": "u3",
             "referenced_tweets": [{"type": "replied_to", "id": "8"}]},
        ]})
        dump(folder, "mentions_1.json", {"data": [
            {"id": "11", "author_id": "u1", "created_at": "2026-11-04T10:00:00Z",
             "text": "reply one", "in_reply_to_user_id": "me",
             "referenced_tweets": [{"type": "replied_to", "id": "1"}],
             "conversation_id": "1"},
            {"id": "12", "author_id": "u1", "created_at": "2026-11-04T10:05:00Z",
             "text": "reply two", "in_reply_to_user_id": "me",
             "referenced_tweets": [{"type": "replied_to", "id": "2"}],
             "conversation_id": "2"},
            {"id": "13", "author_id": "u1", "created_at": "2026-11-04T10:10:00Z",
             "text": "reply on the owner reply", "in_reply_to_user_id": "me",
             "referenced_tweets": [{"type": "replied_to", "id": "9"}],
             "conversation_id": "8"},
        ], "includes": {"users": [{
            "id": "u1", "username": "alice", "name": "Alice",
            "connection_status": ["following", "followed_by"],
        }]}})
        dump(folder, "followers_1.json", {"data": [{"id": "u1", "username": "alice"}]})
        dump(folder, "following_1.json", {"data": [{"id": "u1", "username": "alice"}]})
        data = xn.Data(folder, "me")
        activity = xn.build_activity(
            data, self.since, self.now, 10, 1, 3, "both", self.now, mutual_bonus=10)
        # Two replies on original posts: 2 * 3 + 2 * 10.
        # The reply under the owner's own reply is not in the score.
        self.assertEqual(activity["ranked"]["u1"], 26)

    def test_new_follower_credit_once_for_follows_you(self):
        with redirect_stderr(io.StringIO()) as err:
            ranked = self.ranked(new_follower_credit=5)
        # No snapshot covers the week start, so the follows-you person scores activity only.
        self.assertEqual(ranked["u1"], 7)
        self.assertEqual(ranked["u2"], 8)
        self.assertIn(
            "No follower snapshot at or before the week start.", err.getvalue())

    def test_mutual_new_follower_credited_once(self):
        with redirect_stderr(io.StringIO()):
            ranked = self.ranked(mutual_bonus=2, new_follower_credit=1)
        # u1 is a mutual. Activity 7 plus 2 for each of the two replies on the original.
        # New-follower credit is not added on top of that.
        self.assertEqual(ranked["u1"], 11)
        # u2 follows the owner only, and there is no week-start snapshot. Activity 8.
        self.assertEqual(ranked["u2"], 8)

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
            mutual_bonus=4)
        self.assertEqual(activity["ranked"]["u9"], 7)


def thread_fixture(posts, mentions):
    folder = tempfile.mkdtemp(prefix="xn-thread-")
    dump(folder, "posts_1.json", {"data": posts})
    dump(folder, "mentions_1.json", {
        "data": mentions,
        "includes": {"users": [
            {"id": "alice", "username": "alice", "name": "Alice"},
            {"id": "bob", "username": "bob", "name": "Bob"},
        ]},
    })
    return xn.Data(folder, "me"), folder


def owner_post(pid, created, text="owner original", parent=None):
    row = {
        "id": pid,
        "author_id": "me",
        "created_at": created,
        "text": text,
        "conversation_id": "r1",
    }
    if parent:
        row["referenced_tweets"] = [{"type": "replied_to", "id": parent}]
    return row


def neighbour_reply(pid, author, created, text, parent, root="r1"):
    return {
        "id": pid,
        "author_id": author,
        "created_at": created,
        "text": text,
        "conversation_id": root,
        "referenced_tweets": [{"type": "replied_to", "id": parent}],
    }


class ThreadReplyScoreTest(unittest.TestCase):
    """Nested replies change rank. The report still lists first-level replies only."""

    since = xn.ts("2026-11-04T00:00:00Z")
    now = xn.ts("2026-11-04T18:00:00Z")

    def activity(self, data, reply_weight=3, since=None, **weights):
        return xn.build_activity(
            data, since or self.since, self.now, 10, 1, reply_weight, "both", self.now,
            **weights)

    def test_depth_two_does_not_add_mutual_bonus(self):
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        first = neighbour_reply(
            "a1", "alice", "2026-11-04T10:00:00Z", "alice on the original", "r1")
        deep = neighbour_reply(
            "a2", "alice", "2026-11-04T11:00:00Z", "alice deeper in the thread", "a1")
        data, _folder = thread_fixture([root], [first, deep])
        data.users["alice"]["connection_status"] = ["following", "followed_by"]
        activity = self.activity(
            data, mutual_bonus=10, thread_reply_factor=0.5, author_engaged_bonus=0)
        # One first-level reply is 3, plus mutual_bonus 10 once. The depth-2 reply
        # adds 0.5 * 3 and does not add another 10.
        self.assertEqual(activity["ranked"]["alice"], 14.5)
        self.assertEqual(activity["totals"]["replies"], 1)

    def test_depth_four_adds_nothing(self):
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        chain = [
            neighbour_reply("d1", "alice", "2026-11-04T10:00:00Z", "alice depth one", "r1"),
            neighbour_reply("d2", "alice", "2026-11-04T11:00:00Z", "alice depth two", "d1"),
            neighbour_reply("d3", "alice", "2026-11-04T12:00:00Z", "alice depth three", "d2"),
            neighbour_reply("d4", "alice", "2026-11-04T13:00:00Z", "alice depth four", "d3"),
        ]
        # Depth 1 is 3. Depth 2 and 3 are 0.5 * 3 each. Depth 4 is 0. Total 6.
        full, _folder = thread_fixture([root], chain)
        short, _folder = thread_fixture([root], chain[:-1])
        full_score = self.activity(full, thread_reply_factor=0.5)["ranked"]["alice"]
        short_score = self.activity(short, thread_reply_factor=0.5)["ranked"]["alice"]
        self.assertEqual(full_score, 6)
        self.assertEqual(short_score, 6)

    def test_fourth_depth_two_on_one_root_adds_nothing(self):
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        bob = neighbour_reply("b1", "bob", "2026-11-04T10:00:00Z", "bob on the original", "r1")
        replies = [
            neighbour_reply("c1", "alice", "2026-11-04T11:00:00Z", "alice under bob one", "b1"),
            neighbour_reply("c2", "alice", "2026-11-04T11:10:00Z", "alice under bob two", "b1"),
            neighbour_reply("c3", "alice", "2026-11-04T11:20:00Z", "alice under bob three", "b1"),
            neighbour_reply("c4", "alice", "2026-11-04T11:30:00Z", "alice under bob four", "b1"),
        ]
        four, _folder = thread_fixture([root], [bob] + replies)
        three, _folder = thread_fixture([root], [bob] + replies[:3])
        # Three counted depth-2 replies: 3 * 0.5 * 3 = 4.5. The fourth adds 0.
        four_score = self.activity(four, thread_reply_factor=0.5)["ranked"]["alice"]
        three_score = self.activity(three, thread_reply_factor=0.5)["ranked"]["alice"]
        self.assertEqual(four_score, 4.5)
        self.assertEqual(three_score, 4.5)

    def test_author_engaged_bonus_once(self):
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        owner_reply = owner_post(
            "o1", "2026-11-04T10:00:00Z", text="owner came back", parent="r1")
        later = [
            neighbour_reply("n1", "alice", "2026-11-04T11:00:00Z", "alice after the owner", "o1"),
            neighbour_reply("n2", "alice", "2026-11-04T12:00:00Z", "alice after the owner again", "n1"),
        ]
        since = xn.ts("2026-11-04T10:30:00Z")
        weights = {"thread_reply_factor": 0, "author_engaged_bonus": 2}

        two, _folder = thread_fixture([root, owner_reply], later)
        one, _folder = thread_fixture([root, owner_reply], later[:1])
        quiet, _folder = thread_fixture([root], later)
        # Factor is 0, so the replies add no thread credit. One bonus is 2 * 3 = 6.
        two_score = self.activity(two, since=since, **weights)["ranked"]["alice"]
        one_score = self.activity(one, since=since, **weights)["ranked"]["alice"]
        quiet_score = self.activity(quiet, since=since, **weights)["ranked"]["alice"]
        self.assertEqual(two_score, 6)
        self.assertEqual(one_score, 6)
        self.assertEqual(quiet_score, 0)

    def test_weight_defaults(self):
        for cfg in (
            {},
            {"thread_reply_factor": None, "author_engaged_bonus": None},
            {"thread_reply_factor": "", "author_engaged_bonus": ""},
        ):
            weights = xn.resolve_weights(cfg)
            self.assertEqual(weights["thread_reply_factor"], 0.5)
            self.assertIsInstance(weights["thread_reply_factor"], float)
            self.assertEqual(weights["author_engaged_bonus"], 1.0)
            self.assertIsInstance(weights["author_engaged_bonus"], float)

        root = owner_post("r1", "2026-11-04T09:00:00Z")
        bob = neighbour_reply("b1", "bob", "2026-11-04T10:00:00Z", "bob on the original", "r1")
        alice = neighbour_reply(
            "a2", "alice", "2026-11-04T11:00:00Z", "alice one step down", "b1")
        data, _folder = thread_fixture([root], [bob, alice])
        activity = xn.build_activity(
            data, self.since, self.now, 10, 1, 3, "both", self.now)
        # Default factor 0.5 * reply_weight 3. No owner reply, so no bonus.
        self.assertEqual(activity["ranked"]["alice"], 1.5)

    def test_missing_parent_counts_as_depth_two_and_the_next_reply_still_counts(self):
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        gap = neighbour_reply(
            "g1", "alice", "2026-11-04T10:00:00Z", "alice under a missing parent", "gone")
        under = neighbour_reply(
            "g2", "bob", "2026-11-04T11:00:00Z", "bob under alice", "g1")
        deeper = neighbour_reply(
            "g3", "alice", "2026-11-04T12:00:00Z", "alice two steps under the gap", "g2")
        weights = {"thread_reply_factor": 0.5, "author_engaged_bonus": 0}
        full, folder = thread_fixture([root], [gap, under, deeper])
        short, _folder = thread_fixture([root], [gap, under])
        # The missing parent makes alice depth 2 (1.5). Bob, one step later, is depth 3 (1.5).
        # Alice's next reply is depth 4 and adds 0, so dropping it leaves both scores.
        full_ranked = self.activity(full, **weights)["ranked"]
        short_ranked = self.activity(short, **weights)["ranked"]
        self.assertEqual(full_ranked["alice"], 1.5)
        self.assertEqual(full_ranked["bob"], 1.5)
        self.assertEqual(short_ranked["alice"], 1.5)
        self.assertEqual(short_ranked["bob"], 1.5)

        orphan = {
            "id": "o9",
            "author_id": "bob",
            "created_at": "2026-11-04T10:30:00Z",
            "text": "bob with no conversation",
            "referenced_tweets": [{"type": "replied_to", "id": "gone"}],
        }
        lone, _folder = thread_fixture([root], [orphan])
        lone_ranked = self.activity(lone, **weights)["ranked"]
        self.assertEqual(lone_ranked["bob"], 0)

    def test_loader_scores_a_depth_two_reply_the_report_omits(self):
        # posts JSON -> Data -> ranked. The owner reply has conversation_id and no replied_to.
        root = owner_post("r1", "2026-11-04T09:00:00Z")
        owner = owner_post("o1", "2026-11-04T10:00:00Z", text="owner came back")
        first = neighbour_reply(
            "a1", "alice", "2026-11-04T09:30:00Z", "alice on the original", "r1")
        first["referenced_posts"] = first.pop("referenced_tweets")
        deep = neighbour_reply(
            "a2", "alice", "2026-11-04T11:00:00Z", "alice deeper in the thread", "o1")
        deep["referenced_posts"] = deep.pop("referenced_tweets")
        plain, _folder = thread_fixture([root, owner], [first])
        nested, folder = thread_fixture([root, owner], [first, deep])
        weights = {"thread_reply_factor": 0.5, "author_engaged_bonus": 0}
        self.assertEqual(self.activity(plain, **weights)["ranked"]["alice"], 3)
        activity = self.activity(nested, **weights)
        self.assertEqual(activity["ranked"]["alice"], 4.5)
        self.assertEqual(activity["totals"]["replies"], 1)
        path = os.path.join(folder, "report.md")
        xn.write_chat_report(
            activity, path, "me", self.since, self.now, "Last 24 hours", window="day")
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("alice on the original", report)
        self.assertNotIn("alice deeper in the thread", report)
        waiting, answered = xn.greeting_rows(activity["threads"])
        self.assertEqual([row["text"] for row in waiting + answered], ["alice on the original"])
        reasons = " ".join(
            row["reason"] for row in xn.open_item_rows(nested, activity, None, "me")
            if row["kind"] == "first_reply")
        self.assertIn("alice on the original", reasons)
        self.assertNotIn("alice deeper in the thread", reasons)


if __name__ == "__main__":
    unittest.main()
