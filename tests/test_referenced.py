#!/usr/bin/env python3
"""Loader accepts referenced_posts as well as referenced_tweets."""
import json
import os
import tempfile
import unittest

import x_neighbours as xn


def dump(folder, name, doc):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


class ReferencedPostsTest(unittest.TestCase):
    def test_referenced_reads_either_field(self):
        post = {"referenced_posts": [{"type": "replied_to", "id": 11}]}
        old = {"referenced_tweets": [{"type": "quoted", "id": 12}]}
        self.assertEqual(xn.referenced(post)[0]["id"], "11")
        self.assertEqual(xn.referenced(old)[0]["id"], "12")
        self.assertEqual(xn.referenced({}), [])

    def test_loader_counts_replies_from_referenced_posts(self):
        folder = tempfile.mkdtemp(prefix="xn-refs-")
        dump(folder, "posts_1.json", {
            "data": [{
                "id": "1",
                "author_id": "me",
                "created_at": "2026-11-04T10:00:00Z",
                "text": "hello",
            }],
        })
        dump(folder, "mentions_1.json", {
            "data": [{
                "id": "2",
                "author_id": "u1",
                "created_at": "2026-11-04T11:00:00Z",
                "in_reply_to_user_id": "me",
                "referenced_posts": [{"type": "replied_to", "id": "1"}],
            }],
            "includes": {"users": [{"id": "u1", "username": "ada", "name": "Ada"}]},
        })
        data = xn.Data(folder, "me")
        since = xn.ts("2026-11-04T00:00:00Z")
        until = xn.ts("2026-11-05T00:00:00Z")
        likes, replies = data.window(since, until)
        self.assertEqual(replies["u1"], 1)
        self.assertEqual(data.mentions[0]["referenced_tweets"][0]["id"], "1")

    def test_original_post_skips_reply_marked_as_referenced_posts(self):
        reply = {
            "id": "9",
            "text": "a reply",
            "referenced_posts": [{"type": "replied_to", "id": "1"}],
        }
        quote = {
            "id": "8",
            "text": "a quote",
            "referenced_posts": [{"type": "quoted", "id": "1"}],
        }
        self.assertFalse(xn.is_original_post(reply))
        self.assertTrue(xn.is_original_post(quote))

    def test_owner_reply_from_referenced_posts_marks_engaged(self):
        folder = tempfile.mkdtemp(prefix="xn-refs-eng-")
        dump(folder, "posts_1.json", {
            "data": [{
                "id": "10",
                "author_id": "me",
                "created_at": "2026-11-04T12:00:00Z",
                "referenced_posts": [{"type": "replied_to", "id": "99"}],
            }],
        })
        data = xn.Data(folder, "me")
        self.assertIn("99", data.owner_replied_to)
        self.assertIn("99", data.engaged)


if __name__ == "__main__":
    unittest.main()
