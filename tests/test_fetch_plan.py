#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fetch_plan
import x_neighbours


def _fetch(pages):
    calls = []

    def fetch(token):
        calls.append(token)
        return pages[len(calls) - 1]

    return fetch, calls


class FetchPlanTest(unittest.TestCase):
    def test_week_run_builds_liking_users_request(self):
        params = fetch_plan.liking_users_params("week", "42")
        self.assertEqual(params["id"], "42")
        self.assertNotIn("pagination_token", params)

    def test_refuses_likers_except_week_window(self):
        self.assertIn("day", x_neighbours.WINDOWS)
        self.assertIn("week", x_neighbours.WINDOWS)
        fetch_plan.liking_users_params("week", "42")
        for run in ("day", "first", "fresh", "custom"):
            with self.subTest(run=run):
                with self.assertRaises(fetch_plan.LikerFetchRefused):
                    fetch_plan.liking_users_params(run, "42")

    def test_owner_timeline_request_excludes_retweets(self):
        params = fetch_plan.owner_timeline_params("7", "2026-11-14T00:00:00Z")
        self.assertEqual(params["id"], "7")
        self.assertEqual(params["start_time"], "2026-11-14T00:00:00Z")
        self.assertEqual(params.get("exclude"), "retweets")

    def test_reposted_by_request_omits_connection_status(self):
        params = fetch_plan.reposted_by_params("99")
        self.assertEqual(params["id"], "99")
        self.assertNotIn("connection_status", params["user.fields"].split(","))

    def test_liker_paging_stops_without_next_token(self):
        fetch, calls = _fetch([
            {"data": [{"id": "1"}], "meta": {"next_token": "a"}},
            {"data": [{"id": "2"}], "meta": {}},
            {"data": [{"id": "3"}], "meta": {"next_token": "no"}},
        ])
        got = fetch_plan.page_likers(fetch)
        self.assertEqual([u["id"] for u in got], ["1", "2"])
        self.assertEqual(calls, [None, "a"])

    def test_liker_paging_stops_when_page_adds_nobody(self):
        fetch, calls = _fetch([
            {"data": [{"id": "1"}], "meta": {"next_token": "a"}},
            {"data": [{"id": "1"}], "meta": {"next_token": "b"}},
            {"data": [{"id": "2"}], "meta": {}},
        ])
        got = fetch_plan.page_likers(fetch)
        self.assertEqual([u["id"] for u in got], ["1"])
        self.assertEqual(calls, [None, "a"])

    def test_liker_paging_keeps_going_after_a_short_page(self):
        fetch, calls = _fetch([
            {"data": [{"id": "1"}], "meta": {"next_token": "a"}},
            {"data": [{"id": "2"}], "meta": {"next_token": "b"}},
            {"data": [{"id": "3"}], "meta": {}},
        ])
        got = fetch_plan.page_likers(fetch)
        self.assertEqual([u["id"] for u in got], ["1", "2", "3"])
        self.assertEqual(calls, [None, "a", "b"])


if __name__ == "__main__":
    unittest.main()
