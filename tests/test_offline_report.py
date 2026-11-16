#!/usr/bin/env python3
"""Run a week report from saved JSON, with no X calls."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "offline")
NOW = "2026-11-16T12:00:00Z"


class OfflineWeekReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="xn-offline-")
        data = os.path.join(cls.tmp, "data")
        shutil.copytree(FIXTURES, data)
        out = os.path.join(cls.tmp, "out")
        os.makedirs(out)
        cmd = [
            sys.executable, os.path.join(ROOT, "x_neighbours.py"),
            "--config", os.path.join(data, "offline.json"),
            "--data", data,
            "--out", out,
            "--now", NOW,
            "--windows", "week",
            "--good-neighbours", os.path.join(data, "good_neighbours.json"),
            "--state", os.path.join(out, "state.json"),
        ]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        cls.result = result
        if result.returncode != 0:
            raise AssertionError(
                "offline report failed:\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        cls.output = json.loads(result.stdout)["week"]
        cls.out = out
        with open(cls.output["report_md"], encoding="utf-8") as fh:
            cls.report = fh.read()

    def test_no_crash_and_stdout_is_json(self):
        self.assertEqual(self.result.returncode, 0)
        self.assertIn("replies", self.output)

    def test_all_output_files_are_written(self):
        wanted = {
            "report md": self.output["report_md"],
            "thanks md": self.output["thanks_md"],
            "more json": self.output["more_json"],
            "picture png": self.output["picture"],
            "state json": os.path.join(self.out, "state.json"),
        }
        missing = [name for name, path in wanted.items() if not os.path.isfile(path)]
        self.assertEqual(missing, [], f"missing files: {wanted}")
        self.assertGreater(os.path.getsize(self.output["picture"]), 0)

    def test_waiting_is_counted_once_per_person(self):
        self.assertEqual(self.report.count("Not replied yet"), 2)
        self.assertIn("This mention only has referenced_posts.", self.report)
        self.assertNotIn("First hello from the same person.", self.report)
        self.assertNotIn("Second hello from the same person.", self.report)
        self.assertIn("Third hello from the same person.", self.report)
        self.assertNotIn("Latest note; owner already replied.", self.report)
        self.assertIn("1 already answered.", self.report)

    def test_more_json_has_iso_created_at_from_catch_record(self):
        with open(self.output["more_json"], encoding="utf-8") as fh:
            more = json.load(fh)
        extra = more.get("catch_up") or []
        self.assertTrue(extra, "need more than five catch-up people to hit catch_record")
        for row in extra:
            self.assertTrue(row.get("created_at"))
            self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()
