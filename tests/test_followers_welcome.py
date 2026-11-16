#!/usr/bin/env python3
"""Welcome-list CLI from fictional daily snapshots. Not real follower data."""
import csv
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPS = os.path.join(ROOT, "tests", "fixtures", "welcome", "snapshots")
API = os.path.join(ROOT, "tests", "fixtures", "welcome", "api")
SCRIPT = os.path.join(ROOT, "followers_welcome.py")


class FollowersWelcomeTest(unittest.TestCase):
    def run_cli(self, data=True, since=None):
        out = os.path.join(tempfile.mkdtemp(prefix="xn-welcome-"), "welcome.csv")
        cmd = [sys.executable, SCRIPT, "--snapshots", SNAPS, "--out", out]
        if data:
            cmd += ["--data", API]
        if since:
            cmd += ["--since", since]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return result.stdout.strip(), rows

    def test_new_verified_follower_is_listed(self):
        _summary, rows = self.run_cli()
        by_user = {row["username"]: row for row in rows}
        self.assertIn("carol_ridge", by_user)
        self.assertEqual(by_user["carol_ridge"]["first_seen_date"], "2026-11-03")
        self.assertEqual(by_user["carol_ridge"]["name"], "Carol Ridge")
        self.assertEqual(by_user["carol_ridge"]["verified_type"], "blue")
        self.assertEqual(by_user["carol_ridge"]["followers_count"], "80")

    def test_new_unverified_follower_is_not_listed(self):
        _summary, rows = self.run_cli()
        users = [row["username"] for row in rows]
        self.assertNotIn("dave_plain", users)
        self.assertNotIn("eve_plain", users)

    def test_follower_who_left_and_came_back(self):
        _summary, rows = self.run_cli()
        bob = next(row for row in rows if row["username"] == "bob_marsh")
        self.assertEqual(bob["first_seen_date"], "2026-11-04")
        self.assertEqual(bob["compared_to_date"], "2026-11-03")

    def test_missing_day_compares_to_nearest_earlier(self):
        summary, rows = self.run_cli()
        carol = next(row for row in rows if row["username"] == "carol_ridge")
        self.assertEqual(carol["compared_to_date"], "2026-11-01")
        users = [row["username"] for row in rows]
        self.assertNotIn("trap_page", users)
        self.assertNotIn("trap_csv", users)
        self.assertEqual(summary, "4 verified now, 3 new verified since 2026-11-01")

    def test_engaged_first_yes_no_unknown(self):
        _summary, rows = self.run_cli(data=True)
        by_user = {row["username"]: row["engaged_first"] for row in rows}
        self.assertEqual(by_user["carol_ridge"], "yes")
        self.assertEqual(by_user["bob_marsh"], "no")
        self.assertEqual(by_user["frank_glen"], "no")
        _summary, unknown = self.run_cli(data=False)
        self.assertEqual({row["engaged_first"] for row in unknown}, {"unknown"})


if __name__ == "__main__":
    unittest.main()
