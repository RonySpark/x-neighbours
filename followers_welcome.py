#!/usr/bin/env python3
"""CSV of new verified followers to welcome, from dated snapshot lists.

  python3 followers_welcome.py --snapshots DIR --out PATH [--data DIR] [--since YYYY-MM-DD]

engaged_first is yes if that user engaged in the saved --data at all.
"""
import argparse
import csv
import os
import re
from datetime import datetime

import x_neighbours as xn

DATE_JSON = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
FIELDS = ("first_seen_date", "username", "name", "verified_type",
          "followers_count", "engaged_first", "compared_to_date")


def snapshots(folder):
    """Dated JSON lists of users. Ignore .csv files and a pages/ subfolder."""
    rows = []
    for path, doc in xn.load(os.path.join(folder, "*.json")):
        match = DATE_JSON.match(os.path.basename(path))
        if not match:
            continue
        if not isinstance(doc, list):
            raise SystemExit(f"{path} must be a JSON list of users")
        users = {str(u["id"]): u for u in doc
                 if isinstance(u, dict) and u.get("id") is not None}
        rows.append((match.group(1), users))
    return rows


def engager_ids(data_dir):
    """Liker, replier, reposter, and quoter ids from x_neighbours.Data."""
    data = xn.Data(data_dir, "0")
    ids = set()
    for bucket in (*data.likes.values(), *data.reposts.values()):
        ids.update(bucket)
    for posts in data.quotes.values():
        ids.update(str(p["author_id"]) for p in posts if p.get("author_id"))
    ids.update(str(m["author_id"]) for m in data.mentions if m.get("author_id"))
    return ids


def as_row(date, prev, user, engagers):
    uid = str(user["id"])
    if engagers is None:
        engaged = "unknown"
    else:
        engaged = "yes" if uid in engagers else "no"
    metrics = user.get("public_metrics") or {}
    return {
        "first_seen_date": date,
        "username": user.get("username") or "",
        "name": user.get("name") or "",
        "verified_type": user.get("verified_type") or "",
        "followers_count": metrics.get("followers_count") or 0,
        "engaged_first": engaged,
        "compared_to_date": prev,
    }


def new_verified(snaps, engagers, since=None):
    rows, prev_date, prev_ids, verified_now = [], None, None, 0
    for date, users in snaps:
        ids = set(users)
        if prev_date is not None:
            for uid in sorted(ids - prev_ids, key=lambda u: (users[u].get("username") or "", u)):
                user = users[uid]
                if user.get("verified") and not (since and date < since):
                    rows.append(as_row(date, prev_date, user, engagers))
        prev_date, prev_ids = date, ids
        verified_now = sum(1 for u in users.values() if u.get("verified"))
    return rows, verified_now


def write_csv(path, rows):
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="CSV of new verified followers to welcome.")
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--data")
    parser.add_argument("--since")
    args = parser.parse_args()
    if args.since:
        try:
            datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            parser.error("--since must be YYYY-MM-DD")
    if args.data and not os.path.isdir(args.data):
        parser.error(f"data folder not found: {args.data}")
    snaps = snapshots(args.snapshots)
    if not snaps:
        raise SystemExit(f"no YYYY-MM-DD.json snapshots in {args.snapshots}")
    engagers = engager_ids(args.data) if args.data else None
    rows, verified_now = new_verified(snaps, engagers, args.since)
    write_csv(args.out, rows)
    since = args.since or snaps[0][0]
    print(f"{verified_now} verified now, {len(rows)} new verified since {since}")


if __name__ == "__main__":
    main()
