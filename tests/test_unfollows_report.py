#!/usr/bin/env python3
"""People unfollows in the neighbours report. Handles are synthetic."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-11-15T16:00:00Z"


def write_snap(folder, day, names):
    path = os.path.join(folder, f"{day}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([{"id": name, "username": name, "name": name} for name in names], fh)


def report(snapshot_dir, zone):
    out = tempfile.mkdtemp(prefix="xn-unf-out-")
    data = tempfile.mkdtemp(prefix="xn-unf-data-")
    cmd = [
        sys.executable, os.path.join(ROOT, "x_neighbours.py"),
        "--config", os.path.join(ROOT, "config.example.json"),
        "--handle", "snapcreator",
        "--user-id", "900",
        "--data", data,
        "--snapshot-dir", snapshot_dir,
        "--out", out,
        "--now", NOW,
        "--windows", "day",
        "--no-picture",
    ]
    if zone is not None:
        cmd.extend(["--timezone", zone])
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    payload = json.loads(result.stdout)["day"]
    with open(payload["report_md"], encoding="utf-8") as fh:
        return fh.read()


class PeopleUnfollowsTest(unittest.TestCase):
    def setUp(self):
        self.snap = tempfile.mkdtemp(prefix="xn-unf-snap-")
        write_snap(self.snap, "2026-11-13", ["cara", "alice", "bob"])
        write_snap(self.snap, "2026-11-14", ["alice", "bob"])
        write_snap(self.snap, "2026-11-15", ["alice", "bob"])
        write_snap(self.snap, "2026-11-16", ["bob"])

    def test_default_zone_is_adelaide_and_groups_by_that_date(self):
        text = report(self.snap, None)
        self.assertIn("### Unfollows (Australia/Adelaide)", text)
        self.assertIn("16 Nov: @alice", text)
        self.assertNotIn("@cara", text)
        self.assertNotIn("@bob", text)

    def test_utc_zone_keeps_the_other_day(self):
        text = report(self.snap, "UTC")
        self.assertIn("### Unfollows (UTC)", text)
        self.assertIn("14 Nov: @cara", text)
        self.assertNotIn("@alice", text)


if __name__ == "__main__":
    unittest.main()
