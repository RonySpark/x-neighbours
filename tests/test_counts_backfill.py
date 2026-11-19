#!/usr/bin/env python3
"""counts.csv backfill from synthetic follower snapshots. Not real X data."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = (
    "date", "followers_total", "followers_verified", "following_total", "mutuals",
)


def dump(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def user(uid, username, verified=False):
    return {"id": uid, "username": username, "name": username, "verified": verified}


def write_counts(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_backfill(folder):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "x_neighbours.py"),
         "--backfill-counts", "--snapshot-dir", folder],
        cwd=ROOT, capture_output=True, text=True)


class CountsBackfillTest(unittest.TestCase):
    def test_second_run_matches_and_keeps_existing_dates(self):
        folder = tempfile.mkdtemp(prefix="xn-backfill-")
        dump(os.path.join(folder, "2026-11-14.json"), [user("a", "alice", True)])
        dump(os.path.join(folder, "2026-11-15.json"), [
            user("a", "alice", True),
            user("b", "bob", False),
        ])
        dump(os.path.join(folder, "following", "2026-11-15.json"), {
            "a": user("a", "alice", True),
            "c": user("c", "cara", False),
        })
        path = os.path.join(folder, "counts.csv")
        write_counts(path, [{
            "date": "2026-11-14",
            "followers_total": 999,
            "followers_verified": 7,
            "following_total": 8,
            "mutuals": 9,
        }])
        first = run_backfill(folder)
        self.assertEqual(first.returncode, 0, first.stderr)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        second = run_backfill(folder)
        self.assertEqual(second.returncode, 0, second.stderr)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), body)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([row["date"] for row in rows], ["2026-11-14", "2026-11-15"])
        kept = rows[0]
        self.assertEqual(kept["followers_total"], "999")
        self.assertEqual(kept["mutuals"], "9")
        added = rows[1]
        self.assertEqual(added, {
            "date": "2026-11-15",
            "followers_total": "2",
            "followers_verified": "1",
            "following_total": "2",
            "mutuals": "1",
        })

    def test_missing_counts_file_is_created_and_stable(self):
        folder = tempfile.mkdtemp(prefix="xn-backfill-new-")
        dump(os.path.join(folder, "2026-11-15.json"), [user("b", "bob")])
        path = os.path.join(folder, "counts.csv")
        self.assertFalse(os.path.isfile(path))
        first = run_backfill(folder)
        self.assertEqual(first.returncode, 0, first.stderr)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        second = run_backfill(folder)
        self.assertEqual(second.returncode, 0, second.stderr)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), body)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows, [{
            "date": "2026-11-15",
            "followers_total": "1",
            "followers_verified": "0",
            "following_total": "0",
            "mutuals": "0",
        }])


if __name__ == "__main__":
    unittest.main()
