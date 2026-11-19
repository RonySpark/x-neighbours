#!/usr/bin/env python3
"""New-follower credit comes from follower snapshots. Handles are synthetic."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import timezone

import x_neighbours as xn


def dump(folder, name, doc):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def person(uid, username):
    return {"id": uid, "username": username, "name": username}


class NewFollowerCreditTest(unittest.TestCase):
    def setUp(self):
        note = getattr(xn, "note_missing_follower_baseline", None)
        if note is not None:
            note.done = False

    def score(self, snaps):
        data_dir = tempfile.mkdtemp(prefix="xn-new-")
        snap_dir = tempfile.mkdtemp(prefix="xn-snap-")
        dump(data_dir, "posts_1.json", {"data": [{
            "id": "1", "author_id": "me", "created_at": "2026-11-16T09:00:00Z",
            "text": "hi",
        }]})
        mentions, users = [], []
        for uid, username in (("a", "alice"), ("b", "bob")):
            mentions.append({
                "id": uid, "author_id": uid, "created_at": "2026-11-16T10:00:00Z",
                "text": username, "in_reply_to_user_id": "me",
                "referenced_tweets": [{"type": "replied_to", "id": "1"}],
            })
            users.append(person(uid, username))
        dump(data_dir, "mentions_1.json", {"data": mentions, "includes": {"users": users}})
        for day, rows in snaps:
            dump(snap_dir, f"{day}.json", [person(uid, name) for uid, name in rows])
        data = xn.Data(data_dir, "me", snapshot_dir=snap_dir)
        data.snapshot_folder = snap_dir
        data.score_zone = timezone.utc
        now = xn.ts("2026-11-16T12:00:00Z")
        return xn.build_activity(
            data, xn.ts("2026-11-09T12:00:00Z"), now, 10, 1, 3, "both", now,
            new_follower_credit=5)["ranked"]

    def test_credit_only_for_someone_absent_at_week_start(self):
        with redirect_stderr(io.StringIO()) as err:
            ranked = self.score([
                ("2026-11-09", [("a", "alice")]),
                ("2026-11-16", [("a", "alice"), ("b", "bob")]),
            ])
        self.assertEqual((ranked["a"], ranked["b"]), (3, 8))
        self.assertEqual(err.getvalue(), "")

    def test_no_baseline_snapshot_gives_no_credit_and_one_note(self):
        snaps = [("2026-11-12", [("b", "bob")]), ("2026-11-16", [("b", "bob")])]
        with redirect_stderr(io.StringIO()) as err:
            first = self.score(snaps)["b"]
            second = self.score(snaps)["b"]
        self.assertEqual((first, second), (3, 3))
        self.assertEqual(err.getvalue().strip().splitlines(), [
            "No follower snapshot at or before the week start. New-follower credit is 0.",
        ])


if __name__ == "__main__":
    unittest.main()
