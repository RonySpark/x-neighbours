#!/usr/bin/env python3
"""Echo daily snapshots feed the existing follower and following id sets."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

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
        self.assertIn("### Following vs followers", report)
        self.assertIn(
            "Now (15 Nov): 3 followers · 2 verified · 2 following · 1 mutual · ratio 1.50",
            report,
        )
        self.assertIn("7 days: not enough history", report)
        self.assertIn("30 days: not enough history", report)

    def test_cli_missing_following_says_so(self):
        result, _out = run_report(SNAP_NO_FOLLOWING)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["day"]
        self.assertTrue(payload["following_snapshot_missing"])
        with open(payload["report_md"], encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("Followers (list from 15 Nov)", report)
        self.assertIn("No following snapshot for 15 Nov", report)
        self.assertIn("No counts.csv in the snapshot folder.", report)
        self.assertNotIn("### Following vs followers", report)

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


COUNT_FIELDS = (
    "date", "followers_total", "followers_verified", "following_total", "mutuals",
)
COUNT_START = date(2026, 10, 16)


def write_counts(folder, rows):
    path = os.path.join(folder, "counts.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COUNT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def synthetic_counts(n, start=COUNT_START):
    """Fictional daily totals. Not real follower data."""
    rows = []
    for i in range(n):
        rows.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "followers_total": 100 + i,
            "followers_verified": 10 + i // 2,
            "following_total": 50 + i // 5,
            "mutuals": 20 + i // 3,
        })
    return rows


def section_from(rows_or_path):
    if isinstance(rows_or_path, str):
        rows = xn.load_counts_csv(rows_or_path)
    else:
        rows = rows_or_path
    return "\n".join(xn.counts_section_lines(rows))


class CountsTrendTest(unittest.TestCase):
    def test_one_row_has_latest_and_not_enough_history(self):
        folder = tempfile.mkdtemp(prefix="xn-counts-1-")
        path = write_counts(folder, synthetic_counts(1))
        text = section_from(path)
        self.assertIn("### Following vs followers", text)
        self.assertIn(
            "Now (16 Oct): 100 followers · 10 verified · 50 following · 20 mutuals · ratio 2.00",
            text,
        )
        self.assertIn("7 days: not enough history", text)
        self.assertIn("30 days: not enough history", text)
        self.assertNotIn("now vs", text)

    def test_eight_rows_have_seven_day_change_only(self):
        folder = tempfile.mkdtemp(prefix="xn-counts-8-")
        path = write_counts(folder, synthetic_counts(8))
        text = section_from(path)
        self.assertIn(
            "Now (23 Oct): 107 followers · 13 verified · 51 following · 22 mutuals · ratio 2.10",
            text,
        )
        self.assertIn(
            "7 days: followers +7 · verified +3 · following +1 · mutuals +2. "
            "Ratio 2.10 now vs 2.00 then",
            text,
        )
        self.assertIn("30 days: not enough history", text)

    def test_thirty_one_rows_have_seven_and_thirty_day_change(self):
        folder = tempfile.mkdtemp(prefix="xn-counts-31-")
        path = write_counts(folder, synthetic_counts(31))
        text = section_from(path)
        self.assertIn(
            "Now (15 Nov): 130 followers · 25 verified · 56 following · 30 mutuals · ratio 2.32",
            text,
        )
        self.assertIn(
            "7 days: followers +7 · verified +4 · following +2 · mutuals +3. "
            "Ratio 2.32 now vs 2.28 then",
            text,
        )
        self.assertIn(
            "30 days: followers +30 · verified +15 · following +6 · mutuals +10. "
            "Ratio 2.32 now vs 2.00 then",
            text,
        )
        self.assertNotIn("not enough history", text)

    def test_exact_totals_not_abbreviated(self):
        folder = tempfile.mkdtemp(prefix="xn-counts-exact-")
        path = write_counts(folder, [{
            "date": "2026-11-15",
            "followers_total": 1376,
            "followers_verified": 40,
            "following_total": 1835,
            "mutuals": 200,
        }])
        text = section_from(path)
        self.assertIn(
            "Now (15 Nov): 1,376 followers · 40 verified · 1,835 following · 200 mutuals · ratio 0.75",
            text,
        )
        self.assertNotIn("1.4K", text)
        self.assertNotIn("1.8K", text)

    def test_missing_file_skips_section_with_one_line(self):
        folder = tempfile.mkdtemp(prefix="xn-counts-missing-")
        path = os.path.join(folder, "counts.csv")
        self.assertFalse(os.path.isfile(path))
        text = section_from(path)
        self.assertEqual(text, "No counts.csv in the snapshot folder.")
        self.assertNotIn("### Following vs followers", text)


if __name__ == "__main__":
    unittest.main()
