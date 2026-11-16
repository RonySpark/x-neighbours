#!/usr/bin/env python3
"""Echo daily snapshots feed the existing follower and following id sets."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "tests", "fixtures", "snapshot")
SNAP_NO_FOLLOWING = os.path.join(SNAP, "no-following")
NOW = "2026-11-16T12:00:00Z"


def empty_data():
    return tempfile.mkdtemp(prefix="xn-snap-data-")


def run_report(snapshot_dir, now=NOW):
    out = tempfile.mkdtemp(prefix="xn-snap-out-")
    cmd = [
        sys.executable, os.path.join(ROOT, "x_neighbours.py"),
        "--config", os.path.join(ROOT, "config.example.json"),
        "--handle", "snapcreator",
        "--user-id", "900",
        "--data", empty_data(),
        "--snapshot-dir", snapshot_dir,
        "--out", out,
        "--now", now,
        "--windows", "day",
        "--no-picture",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return result, out


class EchoSnapshotTest(unittest.TestCase):
    def test_newest_date_is_picked(self):
        data = xn.Data(empty_data(), "me", snapshot_dir=SNAP)
        self.assertEqual(data.snapshot_date, "2026-11-15")
        self.assertIn("903", data.followers)
        self.assertNotIn("905", data.followers)
        self.assertIn("904", data.following)
        self.assertNotIn("906", data.following)

    def test_list_followers_and_dict_following(self):
        data = xn.Data(empty_data(), "me", snapshot_dir=SNAP)
        self.assertEqual(data.followers, {"901", "902", "903"})
        self.assertEqual(data.following, {"901", "904"})
        self.assertEqual(data.users["901"]["username"], "ada_vale")
        self.assertEqual(data.users["904"]["username"], "drew_holt")

    def test_missing_following_falls_back_to_empty(self):
        data = xn.Data(empty_data(), "me", snapshot_dir=SNAP_NO_FOLLOWING)
        self.assertEqual(data.snapshot_date, "2026-11-15")
        self.assertTrue(data.snapshot_following_missing)
        self.assertEqual(data.following, set())
        self.assertEqual(data.followers, {"901", "902", "903"})
        self.assertEqual(data.relation("901"), "follows-you")
        self.assertEqual(data.relation("904"), "neither")

    def test_mutuals_from_snapshot(self):
        data = xn.Data(empty_data(), "me", snapshot_dir=SNAP)
        self.assertEqual(data.relation("901"), "mutual")
        self.assertEqual(data.relation("902"), "follows-you")
        self.assertEqual(data.relation("903"), "follows-you")
        self.assertEqual(data.relation("904"), "you-follow")

    def test_snapshot_dir_ignores_followers_json(self):
        folder = empty_data()
        with open(os.path.join(folder, "followers_1.json"), "w", encoding="utf-8") as fh:
            json.dump({"data": [{"id": "999", "username": "trap"}]}, fh)
        data = xn.Data(folder, "me", snapshot_dir=SNAP)
        self.assertNotIn("999", data.followers)
        self.assertIn("901", data.followers)

    def test_cli_report_includes_snapshot_age(self):
        result, _out = run_report(SNAP)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["day"]
        self.assertEqual(payload["snapshot_date"], "2026-11-15")
        self.assertEqual(payload["follower_snapshot_age_days"], 1)
        self.assertNotIn("following_snapshot_missing", payload)
        with open(payload["report_md"], encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("Followers (list from 15 Nov)", report)
        self.assertNotIn("No following snapshot", report)
        self.assertNotIn("follower list is", report)

    def test_cli_missing_following_says_so(self):
        result, _out = run_report(SNAP_NO_FOLLOWING)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["day"]
        self.assertTrue(payload["following_snapshot_missing"])
        with open(payload["report_md"], encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("Followers (list from 15 Nov)", report)
        self.assertIn("No following snapshot for 15 Nov", report)

    def test_stale_snapshot_uses_existing_age_wording(self):
        result, _out = run_report(SNAP, now="2026-11-30T12:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["day"]
        self.assertEqual(payload["follower_snapshot_age_days"], 15)
        with open(payload["report_md"], encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn(
            "Followers (list from 15 Nov) (follower list is 15 days old, "
            "so refresh it for accurate numbers)",
            report,
        )


if __name__ == "__main__":
    unittest.main()
