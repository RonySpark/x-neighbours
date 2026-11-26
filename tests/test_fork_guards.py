#!/usr/bin/env python3
"""Fork guards: default config does not send data, and X stays read-only.

Handles in the offline fixture are fictional. This file does not call X or GitHub.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import community
import neighbour_dog
import share_submission as share
import version as version_mod
import x_neighbours as xn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "offline")
NOW = "2026-11-16T12:00:00Z"

# A write to X would name one of these. List-member tools are not in the list.
X_WRITE = re.compile(
    r"api\.(?:x|twitter)\.com/.*/(?:following|likes|retweets)|"
    r"friendships/create|"
    r"direct_messages|"
    r"dm_conversations|"
    r"statuses/update|"
    r"/2/tweets\b|"
    r"^\s*def\s+(?:follow|unfollow|like_post|unlike|send_dm|post_tweet|create_tweet)\b",
    re.MULTILINE,
)
SKIP_DIRS = {".git", "tests", ".avatar_cache", "examples"}


def python_sources():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and not name.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


class ReadOnlyGuardTest(unittest.TestCase):
    def test_no_follow_like_post_or_dm_code_path(self):
        hits = []
        for path in python_sources():
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if X_WRITE.search(text):
                hits.append(os.path.relpath(path, ROOT))
        self.assertEqual(hits, [])


class DataEgressTest(unittest.TestCase):
    def test_default_config_makes_no_upstream_or_community_call(self):
        calls = []

        def blocked(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(f"network call: {args!r}")

        tmp = tempfile.mkdtemp(prefix="xn-egress-")
        try:
            data = os.path.join(tmp, "data")
            shutil.copytree(FIXTURES, data)
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            config = {
                "handle": "example_owner",
                "user_id": "100",
                "name": "Example Owner",
                "like_weight": 1,
                "reply_weight": 4,
                "repost_weight": 1.5,
                "quote_weight": 4,
                "mutual_bonus": 2,
                "new_follower_credit": 3,
                "thread_reply_factor": 0.5,
                "author_engaged_bonus": 1.0,
                "exclude": ["grok"],
                "rings": 1,
                "show_names": False,
                "windows": ["week"],
                "top": 10,
                "theme": "midnight",
                "report": "both",
                "timezone": "Australia/Adelaide",
            }
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh)
            community_path = os.path.join(tmp, "community.json")
            argv = [
                "x_neighbours.py",
                "--config", config_path,
                "--data", data,
                "--out", out,
                "--now", NOW,
                "--windows", "week",
                "--no-image",
                "--good-neighbours", os.path.join(data, "good_neighbours.json"),
            ]
            with patch("urllib.request.urlopen", blocked), patch.object(sys, "argv", argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    xn.main()
                payload = json.loads(buf.getvalue())["week"]
                self.assertEqual(payload["page_title"], "Period Report")
                self.assertNotIn("share_offer", payload)
                with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
                    self.assertEqual(version_mod.version(), fh.read().strip())
                state = community.load(community_path)
                self.assertFalse(state["community"])
                plan = community.weekly_plan(False)
                self.assertFalse(plan["fetch_global"])
                self.assertFalse(plan["fetch_versions"])
                self.assertFalse(plan["pull"])
                self.assertNotIn("global_url", plan)
                update = version_mod.update_plan("main")
                self.assertFalse(update["pull"])
                self.assertFalse(update["fetch_global"])
                self.assertFalse(update["upgrade"])
                refused = share.create_pull_request(
                    "secret-token", "submissions/100/file.neighbours", "{}",
                    "title", "body", transport=blocked)
                self.assertFalse(refused["opened"])
                self.assertFalse(refused["pull"])
                dog = neighbour_dog.act(
                    {"decision": "merge"}, "secret-token", 1, transport=blocked)
                self.assertFalse(dog["merged"])
                self.assertFalse(dog["posted"])
            self.assertEqual(calls, [])
        finally:
            shutil.rmtree(tmp)

    def test_submit_modules_do_not_call_urlopen(self):
        for name in ("share_submission.py", "neighbour_dog.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("urlopen", text, name)
            self.assertNotIn("api.github.com", text, name)


if __name__ == "__main__":
    unittest.main()
