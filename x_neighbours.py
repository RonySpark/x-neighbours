#!/usr/bin/env python3
"""X Neighbours: who replied, quoted, liked, and reposted, plus an occasional picture.

Settings come from config.json. CLI flags override it. The script does not call X.
It reads raw JSON saved from X tool calls (see README.md) in the data folder:

  posts_*.json         get_users_posts pages (expansions=author_id)
  mentions_*.json      get_users_mentions pages (expansions=author_id)
  likes_<postid>.json  get_posts_liking_users
  reposts_<postid>.json get_posts_reposted_by
  quotes_<postid>.json get_posts_quoted_posts
  latest_<userid>.json get_users_posts for one neighbour (their latest posts)
  my_likes_*.json      get_users_liked_posts (posts the owner already liked)
  my_posts_*.json      get_users_posts for the owner, including replies and reposts

  Followers and following come from Echo's daily snapshot (--snapshot-dir /
  config snapshot_dir). Newest YYYY-MM-DD.json is a JSON list of follower
  user objects. following/YYYY-MM-DD.json is an id-to-user dict. Missing
  following/ for that date leaves following empty. counts.csv is daily
  totals; a missing file skips the trend section with one line.

  When --snapshot-dir is absent, the offline fallback is:
  followers_*.json     saved follower pages
  following_*.json     saved following pages

Usage:
  python3 x_neighbours.py --handle your_handle --user-id your_user_id
                      [--data data] [--snapshot-dir PATH] [--out .]
                      [--now 2026-11-04T18:00:00Z]
                      [--rings 3] [--top 10]
                      [--report likes|replies|both] [--per-post] [--per-day]
                      [--good-neighbours PATH] [--version]
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import version as xn_version


def load(pattern):
    out = []
    for f in sorted(glob.glob(pattern)):
        with open(f, encoding="utf-8") as fh:
            try:
                out.append((f, json.load(fh)))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{f} is not JSON ({exc})") from exc
    return out


def load_soft(pattern):
    """Engagement files are optional. A missing or broken file is ignored."""
    out = []
    for f in sorted(glob.glob(pattern)):
        try:
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


DATE_JSON = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
SNAPSHOT_STALE_DAYS = 7
COUNT_METRICS = (
    ("followers_total", "followers"),
    ("followers_verified", "verified"),
    ("following_total", "following"),
    ("mutuals", "mutuals"),
)
COUNT_FIELDS = tuple(key for key, _label in COUNT_METRICS)
COUNT_WINDOWS = (7, 30)


def as_idset_doc(payload):
    """Turn a JSON list or id-to-user dict into the {data: [...]} shape _idset reads."""
    if isinstance(payload, list):
        users = payload
    elif isinstance(payload, dict):
        users = [user for user in payload.values() if isinstance(user, dict)]
    else:
        users = []
    return {"data": users}


def newest_snapshot_date(folder):
    dates = []
    try:
        names = os.listdir(folder)
    except OSError as exc:
        raise SystemExit(f"snapshot folder not readable: {folder} ({exc})") from exc
    for name in names:
        match = DATE_JSON.match(name)
        if match and os.path.isfile(os.path.join(folder, name)):
            dates.append(match.group(1))
    if not dates:
        raise SystemExit(f"no YYYY-MM-DD.json snapshots in {folder}")
    return max(dates)


def load_echo_snapshot(folder):
    """Newest Echo daily snapshot as (date, followers_doc, following_doc or None)."""
    day = newest_snapshot_date(folder)
    followers_path = os.path.join(folder, f"{day}.json")
    loaded = load(followers_path)
    if not loaded:
        raise SystemExit(f"no YYYY-MM-DD.json snapshots in {folder}")
    _, payload = loaded[0]
    if not isinstance(payload, list):
        raise SystemExit(f"{followers_path} must be a JSON list of users")
    following_path = os.path.join(folder, "following", f"{day}.json")
    following_doc = None
    if os.path.isfile(following_path):
        _, following_payload = load(following_path)[0]
        if not isinstance(following_payload, dict):
            raise SystemExit(f"{following_path} must be a JSON object of id to user")
        following_doc = as_idset_doc(following_payload)
    return day, as_idset_doc(payload), following_doc


def snapshot_age_days(snapshot_date, now):
    day = date.fromisoformat(str(snapshot_date))
    return (now.astimezone(timezone.utc).date() - day).days


def snapshot_age_line(snapshot_date, now, following_missing=False):
    """Same follower-list age wording as content_analytics, plus missing following."""
    day = date.fromisoformat(str(snapshot_date))
    stamp = f"{day.day} {day.strftime('%b')}"
    line = f"Followers (list from {stamp})"
    age = snapshot_age_days(snapshot_date, now)
    if age > SNAPSHOT_STALE_DAYS:
        line += f" (follower list is {age} days old, so refresh it for accurate numbers)"
    if following_missing:
        line += (
            f". No following snapshot for {stamp}; "
            "👤? and mutuals treat following as empty"
        )
    return line


def note_missing_follower_baseline():
    if note_missing_follower_baseline.done:
        return
    note_missing_follower_baseline.done = True
    print(
        "No follower snapshot at or before the week start. New-follower credit is 0.",
        file=sys.stderr,
    )


note_missing_follower_baseline.done = False


def followers_new_this_week(folder, week_start):
    """Ids in the newest snapshot and not in the snapshot on or before week_start.

    None when no snapshot file is dated on or before week_start.
    """
    if not folder or not os.path.isdir(folder):
        return None
    import followers_welcome as fw
    snaps = sorted(fw.snapshots(folder), key=lambda pair: pair[0])
    if not snaps:
        return None
    limit = week_start.isoformat()
    baseline = [users for day, users in snaps if day <= limit]
    if not baseline:
        return None
    return set(snaps[-1][1]) - set(baseline[-1])


def load_counts_csv(path):
    """Parse snapshot counts.csv. Missing or unreadable returns None."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = []
            for raw in csv.DictReader(fh):
                row = _parse_count_row(raw)
                if row:
                    rows.append(row)
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    rows.sort(key=lambda row: row["date"])
    return rows


def _required_count(raw, key):
    text = str(raw.get(key, "")).strip()
    if text == "":
        raise ValueError(key)
    return int(text)


def _optional_count(raw, key):
    text = str(raw.get(key) or "").strip()
    if text == "":
        return None
    return int(text)


def _parse_count_row(raw):
    try:
        day = date.fromisoformat((raw.get("date") or "").strip())
        followers_total = _required_count(raw, "followers_total")
        followers_verified = _required_count(raw, "followers_verified")
        following_total = _optional_count(raw, "following_total")
        mutuals = _optional_count(raw, "mutuals")
    except (TypeError, ValueError, AttributeError):
        return None
    return {
        "date": day,
        "followers_total": followers_total,
        "followers_verified": followers_verified,
        "following_total": following_total,
        "mutuals": mutuals,
    }


def _has_following(row):
    return row.get("following_total") is not None


def _follow_ratio(row):
    following = row.get("following_total")
    if following is None or following <= 0:
        return None
    return row["followers_total"] / following


def _fmt_count(n):
    return f"{int(n or 0):,}"


def _fmt_ratio(value):
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _fmt_delta(n):
    n = int(n or 0)
    if n > 0:
        return f"+{_fmt_count(n)}"
    if n < 0:
        return f"-{_fmt_count(-n)}"
    return "0"


def counts_section_lines(rows):
    """Plain-text following vs followers trend. None or empty is one skip line."""
    if not rows:
        return ["No counts.csv in the snapshot folder."]
    latest = rows[-1]
    stamp = f"{latest['date'].day} {latest['date'].strftime('%b')}"
    now_bits = [
        f"{_fmt_count(latest['followers_total'])} followers",
        f"{_fmt_count(latest['followers_verified'])} verified",
    ]
    now_ratio = None
    if _has_following(latest):
        now_ratio = _follow_ratio(latest)
        now_bits.append(f"{_fmt_count(latest['following_total'])} following")
        mutuals = latest.get("mutuals")
        if mutuals is not None:
            noun = "mutual" if mutuals == 1 else "mutuals"
            now_bits.append(f"{_fmt_count(mutuals)} {noun}")
        now_bits.append(f"ratio {_fmt_ratio(now_ratio)}")
    lines = [
        "### Following vs followers",
        "",
        f"Now ({stamp}): " + " · ".join(now_bits),
    ]
    for days in COUNT_WINDOWS:
        if len(rows) < days + 1:
            lines.append(f"{days} days: not enough history")
            continue
        then = rows[-(days + 1)]
        bits = [
            f"followers {_fmt_delta(latest['followers_total'] - then['followers_total'])}",
            f"verified {_fmt_delta(latest['followers_verified'] - then['followers_verified'])}",
        ]
        ratio = ""
        if _has_following(latest) and _has_following(then):
            bits.append(
                f"following {_fmt_delta(latest['following_total'] - then['following_total'])}"
            )
            if latest.get("mutuals") is not None and then.get("mutuals") is not None:
                bits.append(f"mutuals {_fmt_delta(latest['mutuals'] - then['mutuals'])}")
            then_ratio = _follow_ratio(then)
            ratio = f". Ratio {_fmt_ratio(now_ratio)} now vs {_fmt_ratio(then_ratio)} then"
        lines.append(f"{days} days: " + " · ".join(bits) + ratio)
    return lines


ADELAIDE = ZoneInfo("Australia/Adelaide")


def adelaide_snapshot_date(day_str):
    day = date.fromisoformat(day_str)
    return datetime(day.year, day.month, day.day, 12, tzinfo=ADELAIDE).date()


def count_row_from_snapshot(folder, day, followers):
    verified = sum(1 for user in followers.values() if user.get("verified"))
    row = {
        "date": adelaide_snapshot_date(day),
        "followers_total": len(followers),
        "followers_verified": verified,
        "following_total": None,
        "mutuals": None,
    }
    path = os.path.join(folder, "following", f"{day}.json")
    if not os.path.isfile(path):
        return row
    _, payload = load(path)[0]
    following = {}
    if isinstance(payload, dict):
        following = {str(key): user for key, user in payload.items() if isinstance(user, dict)}
    row["following_total"] = len(following)
    row["mutuals"] = len(set(followers) & set(following))
    return row


def write_counts_csv(path, rows):
    fields = ("date",) + COUNT_FIELDS
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".counts-", suffix=".csv", dir=folder)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "date": row["date"].isoformat(),
                    **{key: "" if row[key] is None else row[key] for key in COUNT_FIELDS},
                })
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def backfill_counts(folder):
    """Add one counts.csv row per snapshot date that is not already present."""
    import followers_welcome as fw
    path = os.path.join(folder, "counts.csv")
    by_date = {row["date"]: row for row in (load_counts_csv(path) or [])}
    for day, users in fw.snapshots(folder):
        stamped = adelaide_snapshot_date(day)
        if stamped in by_date:
            continue
        by_date[stamped] = count_row_from_snapshot(folder, day, users)
    write_counts_csv(path, [by_date[day] for day in sorted(by_date)])
    return path


def ts(s):
    if isinstance(s, datetime):
        when = s
    else:
        when = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def iso_utc(value):
    """Format a datetime or ISO string as UTC Z time via ts(). Empty if missing."""
    if not value:
        return ""
    return ts(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_num(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def referenced(obj):
    """Prefer referenced_tweets; fall back to referenced_posts. Ids are strings."""
    if not isinstance(obj, dict):
        return []
    refs = obj.get("referenced_tweets")
    if not refs:
        refs = obj.get("referenced_posts")
    out = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        ref = dict(ref)
        if ref.get("id") is not None:
            ref["id"] = str(ref["id"])
        out.append(ref)
    return out


def set_referenced(obj):
    """Store referenced posts under referenced_tweets so later reads share one key."""
    refs = referenced(obj)
    if refs:
        obj["referenced_tweets"] = refs
    return obj


def counted(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


class Data:
    def __init__(self, data_dir, me_id, snapshot_dir=None):
        self.data_dir = data_dir
        self.me = str(me_id)
        self.snapshot_date = None
        self.snapshot_following_missing = False
        self.users = {}
        self.posts = {}
        self.mentions = []
        self._mention_ids = set()
        self.likes = {}
        self.reposts = {}
        self.quotes = {}
        self.latest = {}
        self.originals = {}
        self.engaged = set()
        self.owner_replied_to = set()
        self.owner_replied_in = set()
        self._authors = {}
        self._conversations = {}
        self._my_posts = []
        self.owner_original_ids = set()
        self.owner_reply_ids = set()
        self._expanded_posts = []
        self.likes_truncated = []
        for _, doc in load(os.path.join(data_dir, "posts_*.json")):
            for post in doc.get("data") or []:
                if not post.get("id"):
                    continue
                post = set_referenced(dict(post))
                post["id"] = str(post["id"])
                if post.get("author_id") is not None:
                    post["author_id"] = str(post["author_id"])
                if post.get("conversation_id") is not None:
                    post["conversation_id"] = str(post["conversation_id"])
                self._index_post(post)
                if post.get("author_id", self.me) == self.me:
                    self.posts[post["id"]] = post
                    self._note_owner_post(post, assume_owner=True)
            self._users((doc.get("includes") or {}).get("users") or [])
            self._expanded_posts.extend(included_posts(doc))
        for _, doc in load(os.path.join(data_dir, "mentions_*.json")):
            for mention in doc.get("data") or []:
                mention = set_referenced(dict(mention))
                mid = mention.get("id")
                if mid is not None:
                    mid = str(mid)
                    mention["id"] = mid
                    if mid in self._mention_ids:
                        continue
                    self._mention_ids.add(mid)
                if mention.get("author_id") is not None:
                    mention["author_id"] = str(mention["author_id"])
                if mention.get("in_reply_to_user_id") is not None:
                    mention["in_reply_to_user_id"] = str(mention["in_reply_to_user_id"])
                if mention.get("conversation_id") is not None:
                    mention["conversation_id"] = str(mention["conversation_id"])
                self._index_post(mention)
                self.mentions.append(mention)
            self._users((doc.get("includes") or {}).get("users") or [])
            self._expanded_posts.extend(included_posts(doc))
        for path, doc in load(os.path.join(data_dir, "likes_*.json")):
            match = re.search(r"likes_(\d+)", os.path.basename(path))
            if not match:
                raise SystemExit(f"liker file name must contain likes_<post id>: {path}")
            pid = match.group(1)
            users = [self._prepare_user(u) for u in (doc.get("data") or [])]
            self._users(users)
            bucket = self.likes.setdefault(pid, [])
            have = set(bucket)
            for user in users:
                uid = user.get("id")
                if uid and uid not in have:
                    bucket.append(uid)
                    have.add(uid)
        for pid, ids in self.likes.items():
            want = like_count(self.posts.get(pid, {}))
            if want > len(ids):
                self.likes_truncated.append((pid, len(ids), want))
        self._load_user_lists("reposts_*.json", self.reposts, "reposts_<post id>")
        self._load_quotes()
        self._load_latest()
        self._load_engagement()
        if snapshot_dir:
            day, followers_doc, following_doc = load_echo_snapshot(snapshot_dir)
            self.snapshot_date = day
            self.followers = self._idset_from_docs([followers_doc])
            if following_doc is None:
                self.following = set()
                self.snapshot_following_missing = True
            else:
                self.following = self._idset_from_docs([following_doc])
        else:
            self.followers = self._idset("followers_*.json")
            self.following = self._idset("following_*.json")

    def _index_post(self, post):
        if not isinstance(post, dict):
            return
        pid = str(post.get("id") or "")
        if not pid:
            return
        if post.get("author_id") is not None:
            self._authors[pid] = str(post["author_id"])
        conv = post.get("conversation_id")
        if conv:
            self._conversations[pid] = str(conv)
        elif is_original_post(post) and pid not in self._conversations:
            self._conversations[pid] = pid

    def _note_owner_reply(self, post):
        if not isinstance(post, dict):
            return
        author = post.get("author_id")
        if author is not None and str(author) != self.me:
            return
        reply_ids = [str(ref["id"]) for ref in referenced(post)
                     if ref.get("type") == "replied_to" and ref.get("id")]
        if not reply_ids and not post.get("in_reply_to_user_id"):
            return
        person = post.get("in_reply_to_user_id")
        person = str(person) if person else None
        conv = post.get("conversation_id")
        conv = str(conv) if conv else None
        for rid in reply_ids:
            self.owner_replied_to.add(rid)
            if not person:
                person = self._authors.get(rid)
            if not conv:
                conv = self._conversations.get(rid) or rid
        if conv and person and person != self.me:
            self.owner_replied_in.add((conv, person))

    def _prepare_user(self, user):
        user = dict(user)
        if user.get("id") is not None:
            user["id"] = str(user["id"])
        url = user.get("profile_image_url") or ""
        if url and not url.startswith(("http://", "https://")):
            local = url[7:] if url.startswith("file://") else url
            if not os.path.isabs(local):
                local = os.path.join(self.data_dir, local)
            if os.path.isfile(local):
                user["profile_image_url"] = os.path.abspath(local)
        return user

    def _users(self, users):
        for user in users:
            user = self._prepare_user(user)
            uid = user.get("id")
            if not uid:
                continue
            old = self.users.get(uid, {})
            fresh = {k: v for k, v in user.items() if v not in (None, "")}
            self.users[uid] = {**old, **fresh}

    def _load_user_lists(self, pattern, dest, label):
        for path, doc in load(os.path.join(self.data_dir, pattern)):
            match = re.search(r"_(\d+)", os.path.basename(path))
            if not match:
                raise SystemExit(f"{label} file name must include an id: {path}")
            pid = match.group(1)
            users = [self._prepare_user(u) for u in (doc.get("data") or [])]
            self._users(users)
            bucket = dest.setdefault(pid, [])
            have = set(bucket)
            for user in users:
                uid = user.get("id")
                if uid and uid not in have:
                    bucket.append(uid)
                    have.add(uid)

    def _load_quotes(self):
        for path, doc in load(os.path.join(self.data_dir, "quotes_*.json")):
            match = re.search(r"quotes_(\d+)", os.path.basename(path))
            if not match:
                raise SystemExit(f"quote file name must contain quotes_<post id>: {path}")
            pid = match.group(1)
            self._users((doc.get("includes") or {}).get("users") or [])
            bucket = self.quotes.setdefault(pid, [])
            have = {item.get("id") for item in bucket}
            for post in doc.get("data") or []:
                post = set_referenced(dict(post))
                if post.get("id") is None:
                    continue
                post["id"] = str(post["id"])
                if post["id"] in have:
                    continue
                if post.get("author_id") is not None:
                    post["author_id"] = str(post["author_id"])
                if post.get("conversation_id") is not None:
                    post["conversation_id"] = str(post["conversation_id"])
                post["_on_post"] = pid
                self._index_post(post)
                bucket.append(post)
                have.add(post["id"])

    def _load_latest(self):
        for path, doc in load(os.path.join(self.data_dir, "latest_*.json")):
            match = re.search(r"latest_(\d+)", os.path.basename(path))
            if not match:
                continue
            uid = match.group(1)
            self._users((doc.get("includes") or {}).get("users") or [])
            posts = []
            for post in doc.get("data") or []:
                post = set_referenced(dict(post))
                if post.get("id") is None or not post.get("created_at"):
                    continue
                post["id"] = str(post["id"])
                if post.get("author_id") is not None:
                    post["author_id"] = str(post["author_id"])
                author = post.get("author_id")
                if author not in (None, "") and author != uid:
                    continue
                if post.get("in_reply_to_user_id") is not None:
                    post["in_reply_to_user_id"] = str(post["in_reply_to_user_id"])
                if post.get("conversation_id") is not None:
                    post["conversation_id"] = str(post["conversation_id"])
                if is_original_post(post):
                    posts.append(post)
            if not posts:
                continue
            posts.sort(key=lambda post: ts(post["created_at"]), reverse=True)
            merged = {post["id"]: post for post in self.originals.get(uid, [])}
            for post in posts:
                merged[post["id"]] = post
            ordered = sorted(merged.values(), key=lambda post: ts(post["created_at"]), reverse=True)
            self.originals[uid] = ordered
            self.latest[uid] = ordered[0]

    def _add_owner_targets(self, post):
        if not isinstance(post, dict):
            return
        own = str(post.get("id") or "")
        conversation = post.get("conversation_id")
        if conversation and str(conversation) != own:
            self.engaged.add(str(conversation))
        for key in ("in_reply_to_tweet_id", "in_reply_to_status_id"):
            if post.get(key):
                self.engaged.add(str(post[key]))
        for ref in referenced(post):
            if not isinstance(ref, dict) or ref.get("id") is None:
                continue
            kind = (ref.get("type") or "").lower()
            if kind in ("replied_to", "quoted", "retweeted"):
                self.engaged.add(str(ref["id"]))

    def _note_owner_post(self, post, assume_owner=False):
        """Record an owner post as original or as a reply, and who it answered.

        A direct reply is answered when this post's replied_to id is that reply.
        Expanded parents can be sparse, so a known reply is never reclassified
        as an original.
        """
        if not isinstance(post, dict):
            return
        post = set_referenced(dict(post))
        pid = post.get("id")
        if pid is None:
            return
        pid = str(pid)
        author = post.get("author_id")
        if author is None:
            if not assume_owner:
                return
        elif str(author) != self.me:
            return
        reply_targets = referenced_ids(post, "replied_to")
        for target in reply_targets:
            self.owner_replied_to.add(target)
        if pid in self.owner_reply_ids:
            return
        if reply_targets or not is_owner_original_post(post):
            self.owner_reply_ids.add(pid)
            self.owner_original_ids.discard(pid)
            return
        self.owner_original_ids.add(pid)

    def _note_expanded_posts(self):
        for raw in self._expanded_posts:
            if not isinstance(raw, dict):
                continue
            post = set_referenced(dict(raw))
            if post.get("id") is not None:
                post["id"] = str(post["id"])
            if post.get("author_id") is not None:
                post["author_id"] = str(post["author_id"])
            self._note_owner_post(post, assume_owner=False)

    def owner_post_ids(self):
        return set(self.posts) | self.owner_original_ids | self.owner_reply_ids

    def _load_engagement(self):
        """Post ids the owner already liked, replied to, reposted, or quoted.

        Missing files leave the set empty apart from replies already in posts_*.json.
        Replies in my_posts_*.json also mark those ids answered.
        """
        my_posts = []
        for doc in load_soft(os.path.join(self.data_dir, "my_posts_*.json")):
            for raw in doc.get("data") or []:
                if not isinstance(raw, dict):
                    continue
                post = set_referenced(dict(raw))
                if post.get("id") is not None:
                    post["id"] = str(post["id"])
                if post.get("author_id") is not None:
                    post["author_id"] = str(post["author_id"])
                self._note_owner_post(post, assume_owner=True)
                my_posts.append(post)
            self._expanded_posts.extend(included_posts(doc))
        self._note_expanded_posts()
        self.engaged = set(self.owner_replied_to)
        for post in self.posts.values():
            self._add_owner_targets(post)
        for post in my_posts:
            self._index_post(post)
            self._my_posts.append(post)
            self._add_owner_targets(post)
        for doc in load_soft(os.path.join(self.data_dir, "my_likes_*.json")):
            for post in doc.get("data") or []:
                if isinstance(post, dict) and post.get("id") is not None:
                    self.engaged.add(str(post["id"]))
        for post in list(self.posts.values()) + self._my_posts:
            self._note_owner_reply(post)

    def _idset_from_docs(self, docs):
        found = set()
        for doc in docs:
            for user in doc.get("data") or []:
                user = self._prepare_user(user)
                uid = user.get("id")
                if not uid:
                    continue
                found.add(uid)
                self.users.setdefault(uid, user)
        return found

    def _idset(self, pattern):
        return self._idset_from_docs(
            doc for _, doc in load(os.path.join(self.data_dir, pattern)))

    def relation(self, uid):
        follows_you = uid in self.followers
        you_follow = uid in self.following
        status = (self.users.get(uid) or {}).get("connection_status")
        if status:
            if isinstance(status, str):
                status = [status]
            flags = {str(item).lower().replace("-", "_").replace(" ", "_") for item in status}
            if "followed_by" in flags:
                follows_you = True
            if "following" in flags:
                you_follow = True
        if follows_you and you_follow:
            return "mutual"
        if follows_you:
            return "follows-you"
        if you_follow:
            return "you-follow"
        return "neither"

    def _in_window(self, created, since, until):
        if not created:
            return False
        when = ts(created)
        if when < since:
            return False
        if until is not None and when >= until:
            return False
        return True

    def window(self, since, until=None):
        likes, replies = Counter(), Counter()
        for pid, post in self.posts.items():
            if not self._in_window(post.get("created_at"), since, until):
                continue
            for uid in self.likes.get(pid, []):
                if uid != self.me:
                    likes[uid] += 1
        for mention in self.mentions:
            if mention.get("author_id") == self.me:
                continue
            if not self._in_window(mention.get("created_at"), since, until):
                continue
            parent = next(iter(referenced_ids(mention, "replied_to")), None)
            if parent and parent in self.owner_original_ids:
                replies[mention["author_id"]] += 1
        excluded = getattr(self, "exclude", set())
        for counts in (likes, replies):
            for uid in list(counts):
                if self.users.get(uid, {}).get("username", "").lower() in excluded:
                    del counts[uid]
        return likes, replies


def is_original_post(post):
    """A catch-up post is their own writing: not a reply and not a plain repost.

    A quote counts. It is their text, and its conversation id is its own id.
    A quote that is also a reply does not count. A plain repost does not.
    Replies are recognised from in_reply_to_*, a replied_to reference
    (referenced_tweets or referenced_posts), or a conversation_id that is
    not this post's id. The last case is what the API sends when reply
    fields were not requested.
    """
    if not post:
        return False
    if post.get("in_reply_to_user_id"):
        return False
    for key in ("in_reply_to_tweet_id", "in_reply_to_status_id", "in_reply_to_post_id"):
        if post.get(key):
            return False
    text = post.get("text") or ""
    if text.startswith("RT @"):
        return False
    kinds = set()
    for ref in referenced(post):
        kinds.add((ref.get("type") or "").replace("-", "_").lower())
    if kinds & {"replied_to", "reply", "retweeted", "retweet", "reposted", "repost"}:
        return False
    # A quote is their own post. Some payloads set conversation_id to the
    # quoted post; that must not throw the quote out.
    if kinds & {"quoted", "quote"}:
        return True
    conversation = post.get("conversation_id")
    post_id = post.get("id")
    if conversation is not None and post_id is not None and str(conversation) != str(post_id):
        return False
    return True


def is_owner_original_post(post):
    """Owner post that is not itself a reply.

    conversation_id, when present, is the thread root. A reply keeps that root
    even when the saved object has no referenced_tweets of its own.
    """
    if not is_original_post(post):
        return False
    root = post.get("conversation_id")
    if root and str(root) != str(post.get("id")):
        return False
    return True


def referenced_ids(post, kind):
    want = (kind or "").lower()
    found = []
    for ref in referenced(post):
        if (ref.get("type") or "").lower() == want and ref.get("id"):
            found.append(str(ref["id"]))
    return found


def included_posts(doc):
    """Posts expanded onto a response, under includes.tweets or includes.posts."""
    includes = doc.get("includes") if isinstance(doc, dict) else None
    if not isinstance(includes, dict):
        return []
    rows = []
    for key in ("tweets", "posts"):
        chunk = includes.get(key) or []
        if isinstance(chunk, list):
            rows.extend(item for item in chunk if isinstance(item, dict))
    return rows


def like_count(post):
    return (post.get("public_metrics") or {}).get("like_count", 0) or 0


def summary(user):
    metrics = user.get("public_metrics") or {}
    bio = re.sub(r"\s+", " ", (user.get("description") or "").strip())
    bio = re.sub(r"https?://\S+", "", bio).strip()
    if len(bio) > 90:
        bio = bio[:87].rstrip() + "…"
    stats = (f"{fmt_num(metrics.get('followers_count'))} followers · "
             f"{fmt_num(metrics.get('tweet_count'))} posts")
    return f"{bio or '(no bio)'} — {stats}"


def link(user):
    handle = user.get("username", "?")
    return f"[@{handle}](https://x.com/{handle})"


def optional_weight(value, fallback):
    if value is None or value == "":
        return fallback
    return whole_number(value)


def has_weight(cfg, key):
    return key in cfg and cfg.get(key) not in (None, "")


DEPRECATED_WEIGHTS = (
    ("mutual_bonus", "mutual_reply_bonus"),
    ("new_follower_credit", "new_follower_bonus"),
)


def aliased_weight(cfg, kept, old, fallback):
    if has_weight(cfg, old):
        print(f"{old} is deprecated; use {kept}.", file=sys.stderr)
        if not has_weight(cfg, kept):
            return optional_weight(cfg.get(old), fallback)
    return optional_weight(cfg.get(kept), fallback)


def apply_loaded_config(cfg, loaded):
    for kept, old in DEPRECATED_WEIGHTS:
        if has_weight(loaded, old) and not has_weight(loaded, kept):
            cfg.pop(kept, None)
    cfg.update(loaded)
    return cfg


def resolve_weights(cfg):
    like_weight = whole_number(cfg.get("like_weight", 1))
    reply_weight = whole_number(cfg.get("reply_weight", 3))
    return {
        "like_weight": like_weight,
        "reply_weight": reply_weight,
        "repost_weight": optional_weight(cfg.get("repost_weight"), like_weight),
        "quote_weight": optional_weight(cfg.get("quote_weight"), reply_weight),
        "mutual_bonus": aliased_weight(cfg, "mutual_bonus", "mutual_reply_bonus", 0),
        "new_follower_credit": aliased_weight(
            cfg, "new_follower_credit", "new_follower_bonus", 0),
        "thread_reply_factor": optional_weight(cfg.get("thread_reply_factor"), 0.5),
        "author_engaged_bonus": optional_weight(cfg.get("author_engaged_bonus"), 1.0),
    }


def active_counts(likes, replies, report):
    if report == "likes":
        return likes, Counter()
    if report == "replies":
        return Counter(), replies
    return likes, replies


def append_table(lines, title, counts, unit, ranked, data, top, level=2):
    lines += ["#" * level + f" {title}", "",
              f"| # | Who | {unit} | Score | Relationship |",
              "|---|---|---|---|---|"]
    for index, (uid, count) in enumerate(counts.most_common(top), 1):
        user = data.users.get(uid, {"username": uid})
        lines.append(
            f"| {index} | {user.get('name', '')} {link(user)} | {count} | "
            f"{ranked[uid]} | {data.relation(uid)} |"
        )
    if not counts:
        lines.append("| – | none | – | – | – |")
    lines.append("")


def suggestion_detail(report, likes, replies, uid):
    like_text = counted(likes[uid], "like", "likes")
    reply_text = counted(replies[uid], "reply", "replies")
    if report == "likes":
        return like_text
    if report == "replies":
        return reply_text
    return f"{like_text}, {reply_text}"


def kept_user(data, uid):
    if not uid or uid == data.me:
        return False
    handle = data.users.get(uid, {}).get("username", "")
    return handle.lower() not in getattr(data, "exclude", set())


def display_name(data, uid):
    return "@" + data.users.get(uid, {}).get("username", uid)


def each_utc_day(since, until):
    start = since.astimezone(timezone.utc)
    end = until.astimezone(timezone.utc)
    cursor = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    while cursor <= end:
        day_end = cursor + timedelta(days=1)
        yield cursor.date(), max(start, cursor), min(end + timedelta(seconds=1), day_end)
        cursor = day_end


def snippet(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) > 90:
        return text[:87].rstrip() + "…"
    return text


THEMES = {
    "midnight": dict(top=(10, 14, 39), bot=(3, 5, 18), glow=(45, 60, 140), orbit=(120, 140, 220, 55),
                     ring0=(126, 160, 255, 255), ring=(90, 110, 190, 255), label_bg=(5, 8, 25, 170),
                     label=(225, 232, 255, 255), title=(240, 244, 255, 255), sub=(160, 175, 230, 255)),
    "light": dict(top=(248, 250, 255), bot=(226, 233, 246), glow=(255, 255, 255), orbit=(90, 110, 160, 70),
                  ring0=(59, 105, 230, 255), ring=(150, 170, 215, 255), label_bg=(255, 255, 255, 210),
                  label=(25, 35, 60, 255), title=(15, 25, 50, 255), sub=(80, 95, 130, 255)),
    "sunset": dict(top=(58, 18, 74), bot=(232, 106, 72), glow=(255, 170, 110), orbit=(255, 220, 190, 70),
                   ring0=(255, 200, 120, 255), ring=(250, 150, 120, 255), label_bg=(40, 10, 40, 170),
                   label=(255, 240, 225, 255), title=(255, 246, 236, 255), sub=(255, 215, 190, 255)),
}
FONTS = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ("/Library/Fonts/Arial Bold.ttf", "/Library/Fonts/Arial.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial.ttf"),
    ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"),
)
# Used when the UI font has no glyph (Hangul, CJK, kana). First match wins.
CJK_FONTS = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
)
# Color emoji fonts are bitmap faces and only load at a fixed pixel size.
EMOJI_FONTS = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "C:/Windows/Fonts/seguiemj.ttf",
)
EMOJI_FONT_SIZE = 109
_FONT_CACHE = {}
_GLYPH_CACHE = {}
_PROBE_INK = {}


def font(size, bold=True):
    from PIL import ImageFont
    for bold_path, regular_path in FONTS:
        path = bold_path if bold else regular_path
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
        other = regular_path if bold else bold_path
        if os.path.exists(other):
            return ImageFont.truetype(other, size)
    return ImageFont.load_default()


def _cached_font(path, size):
    key = (path, size)
    loaded = _FONT_CACHE.get(key)
    if loaded is not None:
        return loaded
    from PIL import ImageFont
    try:
        loaded = ImageFont.truetype(path, size)
    except OSError:
        loaded = None
    _FONT_CACHE[key] = loaded
    return loaded


def cjk_font(size):
    for path in CJK_FONTS:
        if os.path.exists(path):
            loaded = _cached_font(path, size)
            if loaded is not None:
                return loaded
    return None


def emoji_font():
    for path in EMOJI_FONTS:
        if not os.path.exists(path):
            continue
        loaded = _cached_font(path, EMOJI_FONT_SIZE)
        if loaded is not None:
            return loaded
    return None


def _font_key(face):
    return (getattr(face, "path", None), getattr(face, "size", None), id(face))


def _font_covers(face, char):
    """True when face can draw char. Whitespace and joiners always count."""
    if not char or char.isspace():
        return True
    if unicodedata.category(char) in ("Mn", "Me", "Mc", "Cf", "Cc"):
        return True
    key = _font_key(face) + (char,)
    cached = _GLYPH_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        bbox = face.getbbox(char)
    except (OSError, ValueError):
        _GLYPH_CACHE[key] = False
        return False
    if bbox[3] <= bbox[1]:
        _GLYPH_CACHE[key] = False
        return False
    missing = _notdef_ink(face)
    covered = _ink_bytes(face, char) != missing
    _GLYPH_CACHE[key] = covered
    return covered


def _notdef_ink(face):
    key = _font_key(face)
    cached = _PROBE_INK.get(key)
    if cached is None:
        cached = _ink_bytes(face, "\uffff")
        _PROBE_INK[key] = cached
    return cached


def _ink_bytes(face, char):
    from PIL import Image, ImageDraw
    size = max(8, int(getattr(face, "size", 16) or 16))
    image = Image.new("L", (size * 3, size * 2), 0)
    try:
        ImageDraw.Draw(image).text((1, 1), char, font=face, fill=255)
    except (OSError, ValueError):
        return b""
    return image.tobytes()


def _cluster_continues(nxt, current):
    if current.endswith("\u200d"):
        return True
    if nxt in ("\u200d", "\ufe0e", "\ufe0f"):
        return True
    if unicodedata.category(nxt) in ("Mn", "Me", "Mc"):
        return True
    return "\U0001F3FB" <= nxt <= "\U0001F3FF"


def text_clusters(text):
    """User-visible pieces. Emoji sequences and combining marks stay together."""
    chars = list(text or "")
    clusters = []
    index = 0
    while index < len(chars):
        piece = chars[index]
        index += 1
        if "\U0001F1E6" <= piece <= "\U0001F1FF" and index < len(chars) and "\U0001F1E6" <= chars[index] <= "\U0001F1FF":
            piece += chars[index]
            index += 1
        while index < len(chars) and _cluster_continues(chars[index], piece):
            piece += chars[index]
            index += 1
        clusters.append(piece)
    return clusters


def _is_emoji(cluster):
    for char in cluster:
        code = ord(char)
        if code >= 0x1F000 or code == 0x20E3:
            return True
        if 0x2300 <= code <= 0x23FF or 0x2600 <= code <= 0x27BF or 0x2B00 <= code <= 0x2BFF:
            return True
        if 0x1F1E6 <= code <= 0x1F1FF:
            return True
    return False


class LabelFace:
    """One label size, with a CJK font and a scaled color-emoji font when present."""

    def __init__(self, size, bold=True):
        self.size = max(1, int(size))
        self.bold = bold
        self.primary = font(self.size, bold=bold)
        self.cjk = cjk_font(self.size)
        self.emoji = emoji_font()
        self._sprites = {}

    def kind(self, cluster):
        if not cluster:
            return None
        if _is_emoji(cluster) and self._emoji_covers(cluster):
            return "emoji"
        if self._covers(self.primary, cluster):
            return "primary"
        if self.cjk is not None and self._covers(self.cjk, cluster):
            return "cjk"
        return None

    def _covers(self, face, cluster):
        return all(_font_covers(face, char) for char in cluster)

    def _emoji_covers(self, cluster):
        if self.emoji is None:
            return False
        try:
            bbox = self.emoji.getbbox(cluster)
        except (OSError, ValueError):
            return False
        return bbox[3] > bbox[1]

    def runs(self, text):
        runs = []
        for cluster in text_clusters(text):
            kind = self.kind(cluster)
            if kind is None:
                continue
            if runs and runs[-1][0] == kind and kind != "emoji":
                runs[-1][1] += cluster
            else:
                runs.append([kind, cluster])
        return runs

    def width(self, text):
        total = 0
        for kind, chunk in self.runs(text):
            total += self._chunk_width(kind, chunk)
        return total

    def _chunk_width(self, kind, chunk):
        if kind == "emoji":
            return sum(self._sprite(cluster).width for cluster in text_clusters(chunk))
        face = self.cjk if kind == "cjk" else self.primary
        try:
            return face.getlength(chunk)
        except (OSError, ValueError, AttributeError):
            return float(len(chunk) * self.size * 0.6)

    def _sprite(self, cluster):
        cached = self._sprites.get(cluster)
        if cached is not None:
            return cached
        from PIL import Image, ImageDraw
        bbox = self.emoji.getbbox(cluster)
        width = max(1, bbox[2] - bbox[0])
        height = max(1, bbox[3] - bbox[1])
        sheet = Image.new("RGBA", (width + 2, height + 2), (0, 0, 0, 0))
        ImageDraw.Draw(sheet).text(
            (1 - bbox[0], 1 - bbox[1]), cluster, font=self.emoji, embedded_color=True)
        target_h = max(8, self.size)
        target_w = max(1, round(sheet.width * target_h / sheet.height))
        sheet = sheet.resize((target_w, target_h), Image.Resampling.LANCZOS)
        self._sprites[cluster] = sheet
        return sheet

    def fit(self, text, limit):
        """Shorten text so it fits on one line. Drop characters no font can draw."""
        clusters = [cluster for cluster in text_clusters(text) if self.kind(cluster)]
        if not clusters:
            return ""
        if limit <= 0:
            return ""
        if self._width_of(clusters) <= limit:
            return "".join(clusters)
        ellipsis = "…" if self.kind("…") else ""
        while clusters:
            body = "".join(clusters).rstrip()
            fitted = body + ellipsis if body else ""
            if fitted and self.width(fitted) <= limit:
                return fitted
            clusters.pop()
            while clusters and clusters[-1].isspace():
                clusters.pop()
        return ""

    def _width_of(self, clusters):
        return self.width("".join(clusters))

    def draw(self, draw, image, text, x, y, fill):
        cursor = x
        for kind, chunk in self.runs(text):
            if kind == "emoji":
                for cluster in text_clusters(chunk):
                    sprite = self._sprite(cluster)
                    if image is not None:
                        _paste(image, sprite, cursor, y)
                    cursor += sprite.width
                continue
            face = self.cjk if kind == "cjk" else self.primary
            draw.text((cursor, y), chunk, font=face, fill=fill)
            cursor += self._chunk_width(kind, chunk)


def _paste(image, sprite, x, y):
    try:
        image.paste(sprite, (int(x), int(y)), sprite)
    except (ValueError, IndexError):
        return


def picture_lines(user):
    """Lines under one avatar: display name, then @username.

    A missing or blank name leaves the @username on its own.
    """
    username = re.sub(r"\s+", "", str((user or {}).get("username") or "").strip().lstrip("@"))
    handle = f"@{username}" if username else ""
    name = re.sub(r"\s+", " ", str((user or {}).get("name") or "").replace("\n", " ").replace("\r", " ")).strip()
    if not name:
        return (handle,) if handle else ()
    if handle:
        return (name, handle)
    return (name,)


def fitted_picture_lines(lines, name_face, handle_face, limit):
    """The lines actually drawn. An unrenderable name falls back to @username."""
    lines = tuple(lines or ())
    if not lines:
        return []
    if len(lines) == 1:
        text = name_face.fit(lines[0], limit)
        return [text] if text else []
    name = name_face.fit(lines[0], limit)
    handle = handle_face.fit(lines[1], limit)
    drawn = []
    if name:
        drawn.append(name)
    if handle:
        drawn.append(handle)
    return drawn


def draw_caption(draw, image, lines, name_face, handle_face, x, y, limit,
                 fill_bg, fill_name, fill_handle, scale):
    """One pill under an avatar. Display name on top, @username smaller below."""
    drawn = fitted_picture_lines(lines, name_face, handle_face, limit)
    if not drawn:
        return
    if len(drawn) == 1:
        specs = [(drawn[0], name_face, fill_name)]
    else:
        specs = [(drawn[0], name_face, fill_name), (drawn[1], handle_face, fill_handle)]
    widths = [face.width(text) for text, face, _fill in specs]
    text_width = max(widths) if widths else 0
    pad_x = 6 * scale
    pad_top = 2 * scale
    # The old one-line pill left about 4*scale under the em square so @ and y fit.
    pad_bottom = 4 * scale
    gap = 2 * scale
    block = sum(face.size for _text, face, _fill in specs) + gap * (len(specs) - 1)
    left = x - text_width / 2
    top = y
    box = (left - pad_x, top - pad_top, left + text_width + pad_x, top + block + pad_bottom)
    radius = max(1, (box[3] - box[1]) / 2)
    draw.rounded_rectangle(box, radius=radius, fill=fill_bg)
    cursor = top
    for text, face, fill in specs:
        face.draw(draw, image, text, x - face.width(text) / 2, cursor, fill)
        cursor += face.size + gap


def fetch_avatar(user, cache_dir, size):
    from PIL import Image, ImageDraw
    url = user.get("profile_image_url") or ""
    path = os.path.join(cache_dir, f"{user.get('id', 'x')}.img")
    img = None
    try:
        if not os.path.exists(path) and url:
            if url.startswith(("http://", "https://")):
                remote = url.replace("_normal", "_400x400")
                req = urllib.request.Request(remote, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    blob = resp.read()
                with open(path, "wb") as fh:
                    fh.write(blob)
            elif os.path.isfile(url):
                with open(url, "rb") as src, open(path, "wb") as fh:
                    fh.write(src.read())
        if os.path.exists(path):
            img = Image.open(path).convert("RGB")
    except Exception:
        img = None
    if img is None:
        img = Image.new("RGB", (400, 400), (60, 70, 110))
        draw = ImageDraw.Draw(img)
        letter = (user.get("username") or "?")[:1].upper()
        face = font(200)
        width = draw.textlength(letter, font=face)
        draw.text(((400 - width) / 2, 80), letter, font=face, fill=(230, 235, 255))
    width, height = img.size
    side = min(width, height)
    img = img.crop(((width - side) // 2, (height - side) // 2,
                    (width + side) // 2, (height + side) // 2))
    img = img.resize((size * 2, size * 2), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 2 - 1, size * 2 - 1), fill=255)
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


def ring_geometry(rings):
    """The original ring layout. Three rings is the usual full picture."""
    if rings >= 4:
        return {
            "cy": 0.545,
            "centre": 0.118,
            "per_ring": [8, 14, 20, 26],
            "radius": [0.150, 0.248, 0.340, 0.430],
            "size": [0.062, 0.048, 0.038, 0.031],
            "name_width": [1.7, 1.35, 1.15, 1.05],
        }
    return {
        "cy": 0.535,
        "centre": 0.165,
        "per_ring": [8, 14, 20][:rings],
        "radius": [0.19, 0.30, 0.395][:rings],
        "size": [0.085, 0.064, 0.05][:rings],
        "name_width": [1.45, 1.45, 1.45][:rings],
    }


def layout_for_count(count, rings):
    """Use fewer, larger rings when only a handful of neighbours showed up."""
    rings = max(1, min(int(rings), 4))
    if count <= 0:
        return {
            "cy": 0.52, "centre": 0.30,
            "per_ring": [], "radius": [], "size": [], "name_width": [],
        }
    if count <= 8:
        return {
            "cy": 0.56,
            "centre": 0.26 if count <= 3 else 0.22,
            "per_ring": [8],
            "radius": [0.36 if count <= 4 else 0.34],
            "size": [0.16 if count <= 3 else 0.13 if count <= 5 else 0.11],
            "name_width": [1.8],
        }
    if count <= 22 and rings >= 2:
        return {
            "cy": 0.55,
            "centre": 0.18,
            "per_ring": [8, 14],
            "radius": [0.24, 0.40],
            "size": [0.105, 0.078],
            "name_width": [1.55, 1.4],
        }
    return ring_geometry(rings)


def draw_house_frame(draw, x, y, size, color, width, gap, roof=0.32):
    """Thin home-shaped outline (roof peak + walls + flat base) around a round avatar."""
    half = size / 2 + gap
    eave = y - size * 0.18
    peak = y - half - size * roof
    overhang = half + size * 0.10
    walls = [(x - half, eave), (x - half, y + half), (x + half, y + half), (x + half, eave)]
    draw.line(walls, fill=color, width=width, joint="curve")
    draw.line([(x - overhang, eave + size * 0.06), (x, peak), (x + overhang, eave + size * 0.06)],
              fill=color, width=width, joint="curve")


def draw_circle(data, me_user, scores, path, rings=3, show_names=True, width=1200,
                subtitle="Top neighbours of the last 7 days", theme="midnight"):
    theme_colors = THEMES.get(theme, THEMES["midnight"])
    from PIL import Image, ImageDraw, ImageFilter
    cache = os.path.join(os.path.dirname(os.path.abspath(path)), ".avatar_cache")
    os.makedirs(cache, exist_ok=True)
    scale = 2
    canvas_size = width * scale
    background = Image.new("RGB", (canvas_size, canvas_size))
    top, bottom = theme_colors["top"], theme_colors["bot"]
    draw = ImageDraw.Draw(background)
    for y in range(canvas_size):
        blend = y / canvas_size
        color = tuple(int(top[i] + (bottom[i] - top[i]) * blend) for i in range(3))
        draw.line([(0, y), (canvas_size, y)], fill=color)
    glow = Image.new("L", (canvas_size, canvas_size), 0)
    ImageDraw.Draw(glow).ellipse((canvas_size * .18, canvas_size * .18,
                                  canvas_size * .82, canvas_size * .82), fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(canvas_size * .12))
    background = Image.composite(Image.new("RGB", (canvas_size, canvas_size), theme_colors["glow"]),
                                 background, glow)
    canvas = background.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    ranked = [uid for uid, _points in scores.most_common()
              if uid in data.users and uid != data.me]
    layout = layout_for_count(len(ranked), rings)
    cx, cy = canvas_size // 2, int(canvas_size * layout["cy"])
    per_ring = layout["per_ring"]
    radius = [canvas_size * r for r in layout["radius"]]
    avatar_size = [int(canvas_size * s) for s in layout["size"]]
    for orbit in radius:
        draw.ellipse((cx - orbit, cy - orbit, cx + orbit, cy + orbit),
                     outline=theme_colors["orbit"], width=2 * scale)
    name_faces = []
    handle_faces = []
    for size in avatar_size:
        name_px = max(12, int(size * 0.17))
        name_faces.append(LabelFace(name_px, bold=True))
        handle_faces.append(LabelFace(max(11, int(name_px * 0.72)), bold=False))
    index = 0
    for ring_index in range(len(per_ring)):
        count = min(per_ring[ring_index], max(0, len(ranked) - index))
        if count == 0:
            break
        size = avatar_size[ring_index]
        for slot in range(count):
            uid = ranked[index]
            index += 1
            angle = -math.pi / 2 + 2 * math.pi * slot / count + (ring_index % 2) * math.pi / count
            x = cx + radius[ring_index] * math.cos(angle)
            y = cy + radius[ring_index] * math.sin(angle)
            ring_color = theme_colors["ring0"] if ring_index == 0 else theme_colors["ring"]
            pad = 3 * scale
            draw_house_frame(draw, x, y, size, ring_color, 2 * scale, pad)
            user = data.users[uid]
            avatar = fetch_avatar(user, cache, size)
            canvas.alpha_composite(avatar, (int(x - size / 2), int(y - size / 2)))
            if show_names:
                lines = picture_lines(user)
                wedge = 2 * radius[ring_index] * math.sin(math.pi / max(count, 1))
                limit = min(size * layout["name_width"][ring_index], wedge * 0.92)
                draw_caption(
                    draw, canvas, lines, name_faces[ring_index], handle_faces[ring_index],
                    x, y + size / 2 + pad + 4 * scale, limit,
                    theme_colors["label_bg"], theme_colors["label"], theme_colors["sub"], scale)
    centre = int(canvas_size * layout["centre"])
    draw_house_frame(draw, cx, cy, centre, theme_colors["ring0"], 5 * scale, 4 * scale, roof=0.2)
    canvas.alpha_composite(fetch_avatar(me_user, cache, centre), (cx - centre // 2, cy - centre // 2))
    title_font = font(int(canvas_size * .032))
    subtitle_font = font(int(canvas_size * .016), bold=False)
    title = f"@{me_user.get('username')}'s X Neighbours"
    while title_font.size > 16 and draw.textlength(title, font=title_font) > canvas_size * 0.9:
        title_font = font(title_font.size - 2)
    draw.text(((canvas_size - draw.textlength(title, font=title_font)) / 2, canvas_size * .018),
              title, font=title_font, fill=theme_colors["title"])
    sub_y = canvas_size * .018 + title_font.size + 10 * scale
    while subtitle_font.size > 12 and draw.textlength(subtitle, font=subtitle_font) > canvas_size * 0.9:
        subtitle_font = font(subtitle_font.size - 1, bold=False)
    draw.text(((canvas_size - draw.textlength(subtitle, font=subtitle_font)) / 2, sub_y),
              subtitle, font=subtitle_font, fill=theme_colors["sub"])
    canvas.convert("RGB").resize((width, width), Image.LANCZOS).save(path, optimize=True)


DEFAULTS = {
    "handle": None,
    "user_id": None,
    "like_weight": 1,
    "reply_weight": 3,
    "mutual_bonus": 2,
    "new_follower_credit": 1,
    "waiting_rule": {
        "per_person": True,
        "latest_unreplied": True,
    },
    "repost_weight": None,
    "quote_weight": None,
    "exclude": ["grok"],
    "rings": 3,
    "show_names": True,
    "first_run_days": 2,
    "items_per_user": 3,
    "theme": "midnight",
    "windows": ["week"],
    "top": 10,
    "report": "both",
    "per_post": False,
    "per_day": False,
    "good_neighbours": None,
    "snapshot_dir": None,
    "timezone": "",
    "fresh_hours": None,
    "state": None,
}
OPEN_ITEM_FIELDS = ("kind", "handle", "reason", "link", "status")
OPEN_ITEM_KEEP = {"done", "dropped"}
WINDOWS = {
    "day": ("last 24 hours", 1, "Top neighbours of the last 24 hours"),
    "week": ("last 7 days", 7, "Top neighbours of the last 7 days"),
}
REPORTS = ("likes", "replies", "both")
SUBTITLES = {
    "both": {
        "day": "Top neighbours of the last 24 hours",
        "week": "Top neighbours of the last 7 days",
    },
    "likes": {
        "day": "Top likers of the last 24 hours",
        "week": "Top likers of the last 7 days",
    },
    "replies": {
        "day": "Top repliers of the last 24 hours",
        "week": "Top repliers of the last 7 days",
    },
}


def whole_number(value):
    number = float(value)
    return int(number) if number == int(number) else number


def hidden_user(data, uid):
    if not uid or uid == data.me:
        return True
    return data.users.get(uid, {}).get("username", "").lower() in getattr(data, "exclude", set())


def stranger(data, uid):
    return uid not in data.following


def ago(when, now):
    seconds = max(0, (now - when).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{max(1, int(seconds // 3600))}h ago"
    return f"{max(1, int(seconds // 86400))}d ago"


def utc_now():
    """Wall clock in UTC. Reply ages use this, not the report window end."""
    return datetime.now(timezone.utc)


def post_text(data, pid):
    text = snippet((data.posts.get(pid) or {}).get("text"))
    return text or "your post"


def status_url(data, uid, post_id):
    handle = data.users.get(uid, {}).get("username", uid)
    return f"https://x.com/{handle}/status/{post_id}"


def load_good_neighbours(path):
    """Personal list. A missing file is an empty list. At most 20 people."""
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("neighbours") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    people = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("id") or not item.get("username"):
            continue
        people.append({
            "id": str(item["id"]),
            "username": str(item["username"]).lstrip("@"),
            "name": item.get("name") or str(item["username"]).lstrip("@"),
            "added": item.get("added") or "",
        })
        if len(people) >= 20:
            break
    return people


def good_neighbour_ids(neighbours):
    ids = {person["id"] for person in neighbours}
    names = {person["username"].lower() for person in neighbours}
    return ids, names


def is_good_neighbour(uid, username, ids, names):
    return uid in ids or (username or "").lower() in names


def waiting_rule_from(value):
    rule = dict(DEFAULTS["waiting_rule"])
    if isinstance(value, dict):
        for key in rule:
            if key in value:
                rule[key] = as_bool(value[key])
    return rule


THREAD_REPLY_CAP = 3


def thread_reply_credit(data, since, until, reply_weight, thread_reply_factor,
                        author_engaged_bonus):
    posts = {}

    def parent_of(post):
        replied = referenced_ids(post, "replied_to")
        if replied:
            return replied[0]
        for key in ("in_reply_to_tweet_id", "in_reply_to_status_id", "in_reply_to_post_id"):
            value = post.get(key)
            if value:
                return str(value)
        return None

    def add(raw):
        if not isinstance(raw, dict) or raw.get("id") is None:
            return
        post = set_referenced(dict(raw))
        post["id"] = str(post["id"])
        if post.get("author_id") is not None:
            post["author_id"] = str(post["author_id"])
        if post.get("conversation_id") is not None:
            post["conversation_id"] = str(post["conversation_id"])
        pid = post["id"]
        current = posts.get(pid)
        if current is None or (parent_of(current) is None and parent_of(post) is not None):
            posts[pid] = post

    for source in data.posts.values():
        add(source)
    for source in data.mentions:
        add(source)
    for bucket in data.quotes.values():
        for source in bucket:
            add(source)
    for source in data._my_posts:
        add(source)
    for source in data._expanded_posts:
        add(source)

    roots = {str(pid) for pid in data.owner_original_ids}
    me = str(data.me)
    owned = {str(pid) for pid in data.posts}
    for source in data._my_posts:
        if isinstance(source, dict) and source.get("id") is not None:
            owned.add(str(source["id"]))

    def walk(post):
        hops = 0
        current = str(post["id"])
        seen = set()
        while current not in seen:
            seen.add(current)
            node = posts.get(current)
            if node is None:
                return None
            parent = parent_of(node)
            if not parent:
                return None
            hops += 1
            if parent in roots:
                return hops, parent
            if parent not in posts:
                return None
            current = parent
        return None

    def place(post):
        conv = post.get("conversation_id")
        if conv is not None and conv not in roots:
            return None
        parent = parent_of(post)
        if not parent:
            return None
        if parent in roots:
            return 1, conv or parent
        if parent not in posts:
            if conv is None:
                return None
            return 2, conv
        found = walk(post)
        if not found:
            return None
        depth, walked = found
        return depth, conv or walked

    def owner_authored(post):
        author = post.get("author_id")
        if author is not None:
            return str(author) == me
        return str(post.get("id")) in owned

    def owner_reply_roots(post):
        if not owner_authored(post):
            return []
        pid = str(post["id"])
        found = []
        conv = post.get("conversation_id")
        if conv is not None and conv in roots and pid != conv:
            found.append(conv)
        parent = parent_of(post)
        walked = None
        if parent in roots:
            walked = parent
        elif parent and parent in posts:
            chain = walk(post)
            if chain:
                walked = chain[1]
        if walked and pid != walked and walked not in found:
            found.append(walked)
        return found

    scored = []
    for post in posts.values():
        if parent_of(post) is None:
            continue
        author = str(post.get("author_id") or "")
        if hidden_user(data, author):
            continue
        if not data._in_window(post.get("created_at"), since, until):
            continue
        placed = place(post)
        conv = post.get("conversation_id")
        if conv is not None:
            if conv not in roots:
                continue
            root = conv
        elif placed:
            root = placed[1]
        else:
            continue
        depth = placed[0] if placed else None
        scored.append((author, root, depth, ts(post["created_at"]), post["id"]))

    buckets = defaultdict(list)
    for author, root, depth, when, pid in scored:
        if depth in (2, 3):
            buckets[(author, root)].append((when, pid))

    credit = defaultdict(float)
    factor_points = thread_reply_factor * reply_weight
    if factor_points:
        for (author, _root), rows in buckets.items():
            rows.sort()
            credit[author] += factor_points * len(rows[:THREAD_REPLY_CAP])

    bonus_points = author_engaged_bonus * reply_weight
    if bonus_points:
        owner_times = defaultdict(list)
        for post in posts.values():
            created = post.get("created_at")
            if not created:
                continue
            when = ts(created)
            for root in owner_reply_roots(post):
                owner_times[root].append(when)
        granted = set()
        for author, root, _depth, when, _pid in scored:
            if (author, root) in granted:
                continue
            if any(when > owner_when for owner_when in owner_times.get(root, ())):
                granted.add((author, root))
                credit[author] += bonus_points
    return {uid: value for uid, value in credit.items() if value}


def build_activity(data, since, until, top, like_weight, reply_weight, mode, now,
                   good_neighbours=None, mutual_bonus=0, new_follower_credit=0,
                   repost_weight=None, quote_weight=None,
                   thread_reply_factor=0.5, author_engaged_bonus=1.0):
    if repost_weight is None:
        repost_weight = like_weight
    if quote_weight is None:
        quote_weight = reply_weight
    thread_reply_factor = optional_weight(thread_reply_factor, 0.5)
    author_engaged_bonus = optional_weight(author_engaged_bonus, 1.0)
    items = []
    seen = set()

    def add_item(kind, post, author, on_id):
        if hidden_user(data, author) or post.get("id") in seen:
            return
        if not data._in_window(post.get("created_at"), since, until):
            return
        seen.add(post["id"])
        when = ts(post["created_at"])
        conversation = post.get("conversation_id")
        items.append({
            "kind": kind,
            "id": post["id"],
            "author_id": author,
            "text": snippet(post.get("text")) or "(no text)",
            "created_at": when,
            "on_id": on_id,
            "on_text": post_text(data, on_id) if on_id else "your post",
            "url": status_url(data, author, post["id"]),
            "conversation_id": str(conversation) if conversation else str(on_id or post.get("id") or ""),
            "replied": post["id"] in data.owner_replied_to,
        })

    owner_posts = data.owner_post_ids()
    for mention in data.mentions:
        author = mention.get("author_id")
        parent = next(iter(referenced_ids(mention, "replied_to")), None)
        # Top-level only: the direct parent is one of the owner's original posts.
        # A later reply in the thread, whose parent is the owner's own reply, does not count.
        if parent and parent in data.owner_original_ids:
            add_item("reply", mention, author, parent)
            continue
        if parent:
            continue
        quoted = next((pid for pid in referenced_ids(mention, "quoted") if pid in owner_posts), None)
        if quoted:
            add_item("quote", mention, author, quoted)
    for pid, quotes in data.quotes.items():
        for quote in quotes:
            parent = next(iter(referenced_ids(quote, "replied_to")), None)
            if parent and parent not in data.owner_original_ids:
                continue
            if parent:
                add_item("reply", quote, quote.get("author_id"), parent)
            else:
                add_item("quote", quote, quote.get("author_id"), quote.get("_on_post") or pid)

    likes_c, reposts_c = Counter(), Counter()
    reply_c, quote_c = Counter(), Counter()
    for pid, post in data.posts.items():
        if not data._in_window(post.get("created_at"), since, until):
            continue
        for uid in data.likes.get(pid, []):
            if not hidden_user(data, uid):
                likes_c[uid] += 1
        for uid in data.reposts.get(pid, []):
            if not hidden_user(data, uid):
                reposts_c[uid] += 1
    mutual_replies = Counter()
    for item in items:
        (quote_c if item["kind"] == "quote" else reply_c)[item["author_id"]] += 1
        if item["kind"] != "reply":
            continue
        on_id = item.get("on_id")
        on_post = data.posts.get(on_id) if on_id else None
        if on_post and is_original_post(on_post) and data.relation(item["author_id"]) == "mutual":
            mutual_replies[item["author_id"]] += 1

    zone = getattr(data, "score_zone", None) or timezone.utc
    week_start = (now - timedelta(days=7)).astimezone(zone).date()
    fresh = followers_new_this_week(getattr(data, "snapshot_folder", None), week_start)
    if fresh is None:
        if new_follower_credit:
            note_missing_follower_baseline()
        fresh = set()
    credit = thread_reply_credit(
        data, since, until, reply_weight, thread_reply_factor, author_engaged_bonus)

    def points(uid, which):
        likes = likes_c[uid] if which != "replies" else 0
        reposts = reposts_c[uid] if which != "replies" else 0
        replies = reply_c[uid] if which != "likes" else 0
        quotes = quote_c[uid] if which != "likes" else 0
        if which == "likes":
            replies = quotes = 0
        if which == "replies":
            likes = reposts = 0
        total = (likes * like_weight
                 + reposts * repost_weight
                 + replies * reply_weight
                 + quotes * quote_weight)
        if which != "likes":
            total += credit.get(uid, 0)
        relation = data.relation(uid)
        if relation == "mutual" and which != "likes":
            total += mutual_replies[uid] * mutual_bonus
        elif relation == "follows-you" and uid in fresh:
            total += new_follower_credit
        return total

    good = good_neighbours or []
    good_ids, good_names = good_neighbour_ids(good)

    def describe(uid, fallback=None):
        user = data.users.get(uid) or fallback or {"id": uid, "username": uid, "name": uid}
        username = user.get("username") or (fallback or {}).get("username") or uid
        name = user.get("name") or (fallback or {}).get("name") or username
        return {
            "id": uid,
            "name": name,
            "username": username,
            "stranger": uid not in data.following,
            "good": is_good_neighbour(uid, username, good_ids, good_names),
            "user": user if user.get("id") else {**user, "id": uid, "username": username, "name": name},
        }

    people = set(likes_c) | set(reposts_c) | set(reply_c) | set(quote_c)
    people.update(credit)
    ranked = Counter()
    order = sorted(people, key=lambda uid: (-points(uid, "both"), -reply_c[uid] - quote_c[uid], uid))
    for uid in order:
        ranked[uid] = points(uid, "both")
    catch_ranked = sorted(people, key=lambda uid: (-points(uid, mode), -reply_c[uid] - quote_c[uid], uid))

    groups = defaultdict(list)
    for item in items:
        groups[item["author_id"]].append(item)
    threads = []
    for uid, group in groups.items():
        group.sort(key=lambda item: item["created_at"], reverse=True)
        threads.append({
            **describe(uid),
            "items": group,
        })
    threads.sort(key=lambda thread: (
        -len(thread["items"]),
        -thread["items"][0]["created_at"].timestamp(),
        thread["username"],
    ))
    if mode == "likes":
        threads = []

    interactions = []
    for uid in people:
        reply_n = 0 if mode == "likes" else reply_c[uid] + quote_c[uid]
        like_n = 0 if mode == "replies" else likes_c[uid]
        repost_n = 0 if mode == "replies" else reposts_c[uid]
        if mode == "likes" and like_n + repost_n == 0:
            continue
        if mode == "replies" and reply_n == 0:
            continue
        if reply_n + like_n + repost_n <= 0:
            continue
        interactions.append({
            **describe(uid),
            "replies": reply_n,
            "likes": like_n,
            "reposts": repost_n,
        })
    interactions.sort(key=lambda row: (
        -(row["replies"] + row["likes"] + row["reposts"]),
        -row["replies"],
        -row["likes"],
        -row["reposts"],
        row["username"],
    ))

    def catch_person(uid, fallback=None):
        chosen = None
        for post in data.originals.get(uid) or []:
            if not is_original_post(post):
                continue
            if str(post["id"]) in data.engaged:
                continue
            chosen = post
            break
        if chosen is None:
            return None
        return {
            **describe(uid, fallback),
            "latest": chosen,
            "latest_url": status_url(data, uid, chosen["id"]),
        }

    catch_up = []
    if good:
        for neighbour in good:
            person = catch_person(neighbour["id"], neighbour)
            if person:
                catch_up.append(person)
    else:
        for uid in catch_ranked:
            if points(uid, mode) <= 0:
                continue
            person = catch_person(uid)
            if not person:
                continue
            catch_up.append(person)
            if len(catch_up) >= top:
                break

    return {
        "totals": {
            "replies": sum(1 for item in items if item["kind"] == "reply"),
            "quotes": sum(1 for item in items if item["kind"] == "quote"),
            "likes": sum(likes_c.values()),
            "reposts": sum(reposts_c.values()),
        },
        "threads": threads,
        "interactions": interactions,
        "catch_up": catch_up,
        "ranked": ranked,
        "has_good_neighbours": bool(good),
        "catch_hint": not good,
        "all_caught_up": not catch_up and bool(good),
        "owner_replied_in": getattr(data, "owner_replied_in", set()),
    }


def owner_zone(name):
    text = (name or "").strip()
    if not text:
        return timezone.utc
    try:
        return ZoneInfo(text)
    except Exception as exc:
        raise SystemExit(f"Unknown timezone: {name}") from exc


def suggest_good_neighbours(activity, neighbours, limit=5, top=10):
    """Up to five people from Flowers who are not already good neighbours."""
    ids, names = good_neighbour_ids(neighbours or [])
    picked = []
    for row in activity["interactions"][:top]:
        if is_good_neighbour(row["id"], row["username"], ids, names):
            continue
        picked.append({
            "id": row["id"],
            "username": row["username"],
            "name": row["name"],
            "label": suggestion_label(row),
            "avatar": avatar_url(row),
        })
        if len(picked) >= limit:
            break
    return picked


def user_markdown(person):
    star = " ⭐" if person.get("good") else ""
    mark = " 👤?" if person.get("stranger") else ""
    return f"**{person['name']}**{star} [@{person['username']}](https://x.com/{person['username']}){mark}"


def on_post_line(text):
    text = text or "your post"
    if not text.endswith("…"):
        text += "…"
    return f"↳ on your post: “{text}”"


CHAT_SHOW = 5
THANK_YOU_NOTE = "Save the picture and attach it in X before posting."


def elapsed_phrase(since, now):
    """Compact elapsed time. Uses the clock difference, not a calendar date."""
    seconds = max(0, (now - since).total_seconds())
    minutes = int(round(seconds / 60))
    if minutes < 60:
        minutes = max(minutes, 1 if seconds else 0)
        if minutes == 1:
            return "1 min ago"
        return f"{minutes} min ago"
    hours, mins = divmod(minutes, 60)
    if hours < 48:
        hr = "1 hr" if hours == 1 else f"{hours} hr"
        if mins == 0:
            return f"{hr} ago"
        mn = "1 min" if mins == 1 else f"{mins} min"
        return f"{hr} {mn} ago"
    days, rem_h = divmod(hours, 24)
    if rem_h == 0:
        return "1 day ago" if days == 1 else f"{days} days ago"
    hr = "1 hr" if rem_h == 1 else f"{rem_h} hr"
    day = "1 day" if days == 1 else f"{days} days"
    return f"{day} {hr} ago"


def period_line(window, since, now, label):
    if window == "fresh":
        return f"Since your last report · {elapsed_phrase(since, now)}"
    if window == "day":
        return "Last 24 hours"
    if window == "week":
        return "Last 7 days"
    if window == "first":
        span = label or "Last 2 days"
        if span.lower().startswith("last "):
            span = span[0].lower() + span[1:]
        return f"First report · {span}"
    if window == "custom":
        return label or "Custom period"
    return label or "This report"


def count_line(totals):
    return (
        f"💬 {totals.get('replies') or 0} · "
        f"🔁💬 {totals.get('quotes') or 0} · "
        f"❤️ {totals.get('likes') or 0} · "
        f"🔁 {totals.get('reposts') or 0}"
    )


def avatar_url(person):
    """Profile image from saved user data. Not rendered in the chat text."""
    user = person.get("user") or {}
    url = user.get("profile_image_url") or person.get("profile_image_url") or ""
    return url or None


def suggestion_label(row):
    name = row.get("name") or row.get("username") or ""
    label = f"{name} (@{row['username']})"
    if row.get("good"):
        return f"⭐ {label}"
    return label


def person_marks(person):
    bits = []
    if person.get("good"):
        bits.append("⭐")
    if person.get("stranger"):
        bits.append("👤?")
    return (" " + " ".join(bits)) if bits else ""


def hard_break(line):
    """Two trailing spaces: a Markdown hard line break, not a soft wrap."""
    return line.rstrip() + "  "


def flower_lines(row):
    counts = []
    if row.get("replies"):
        counts.append(counted(row["replies"], "reply", "replies"))
    if row.get("likes"):
        counts.append(counted(row["likes"], "like", "likes"))
    if row.get("reposts"):
        counts.append(counted(row["reposts"], "repost", "reposts"))
    if not counts:
        counts.append("showed up")
    name = f"**[{row['name']}](https://x.com/{row['username']})**{person_marks(row)}"
    return [hard_break(name), " · ".join(counts), ""]


def flower_record(row):
    return {
        "name": row["name"],
        "username": row["username"],
        "good": bool(row.get("good")),
        "stranger": bool(row.get("stranger")),
        "replies": row.get("replies") or 0,
        "likes": row.get("likes") or 0,
        "reposts": row.get("reposts") or 0,
        "avatar": avatar_url(row),
    }


def catch_lines(person, now=None):
    """One What's new block.

    `now` is the report window end passed as `--now`. Ages use utc_now()
    so a stale window cannot show a week-old post as "8h ago". ago() itself
    is unchanged.
    """
    del now
    latest = person.get("latest") or {}
    text = snippet(latest.get("text")) or "Post"
    when = ""
    if latest.get("created_at"):
        when = ago(ts(latest["created_at"]), utc_now())
    tail = f" · {when}" if when else ""
    return [
        hard_break(f"**{person['name']}**{person_marks(person)}"),
        f"[“{text}”]({person['latest_url']}){tail}",
        "",
    ]


def catch_record(person):
    latest = person.get("latest") or {}
    return {
        "name": person["name"],
        "username": person["username"],
        "good": bool(person.get("good")),
        "stranger": bool(person.get("stranger")),
        "text": snippet(latest.get("text")) or "Post",
        "url": person.get("latest_url") or "",
        "created_at": iso_utc(latest.get("created_at")),
        "avatar": avatar_url(person),
    }


def thread_key(item):
    """Conversation for the waiting rule.

    Items with no conversation id and no parent id are one thread. That is
    how a flat per-person list decides from the latest message.
    """
    conv = item.get("conversation_id")
    if conv:
        return conv
    on_id = item.get("on_id")
    if on_id:
        return on_id
    return ""


def greeting_rows(threads, waiting_rule=None, owner_replied_in=None):
    """Waiting rows for first-level replies and quotes.

    The caller has already dropped nested replies. Default
    (`waiting_rule.per_person`): one row per person, waiting only if their
    latest message in that thread has no owner reply. Pass a set of
    `(conversation_id, user_id)` as the second argument to emit one row per
    person per conversation.
    """
    per_conversation = False
    if isinstance(waiting_rule, (set, frozenset)):
        owner_replied_in, waiting_rule = waiting_rule, None
        per_conversation = True
    rule = waiting_rule_from(waiting_rule)
    owner_replied_in = owner_replied_in or set()
    unanswered, answered = [], []

    def when_of(item):
        created = item.get("created_at")
        return ts(created) if created else datetime.min.replace(tzinfo=timezone.utc)

    def make_row(thread, item, replied=None):
        created = item.get("created_at")
        if replied is None:
            replied = bool(item.get("replied"))
        return {
            "name": thread["name"],
            "username": thread["username"],
            "good": bool(thread.get("good")),
            "stranger": bool(thread.get("stranger")),
            "kind": item.get("kind") or "reply",
            "text": snippet(item.get("text")) or "Post",
            "url": item.get("url") or "",
            "on_text": snippet(item.get("on_text") or ""),
            "created_at": iso_utc(created),
            "replied": replied,
            "avatar": avatar_url(thread),
            "_when": when_of(item),
        }

    for thread in threads:
        items = list(thread.get("items") or [])
        if not items:
            continue
        uid = str(thread.get("id") or "")
        if not rule["per_person"] and not per_conversation:
            for item in items:
                conv = str(thread_key(item) or "")
                replied = (conv, uid) in owner_replied_in or bool(item.get("replied"))
                row = make_row(thread, item, replied)
                (answered if row["replied"] else unanswered).append(row)
            continue
        groups = defaultdict(list)
        for item in items:
            groups[thread_key(item)].append(item)
        waiting, done = [], []
        for conv_key, group in groups.items():
            group.sort(key=when_of, reverse=True)
            conv = str(conv_key or "")
            owner_hit = (conv, uid) in owner_replied_in
            if rule["latest_unreplied"]:
                latest = group[0]
                replied = owner_hit or bool(latest.get("replied"))
                (done if replied else waiting).append((latest, replied))
            else:
                open_items = [
                    item for item in group
                    if not (owner_hit or item.get("replied"))
                ]
                if open_items:
                    waiting.append((open_items[0], False))
                else:
                    done.append((group[0], True))
        if per_conversation:
            for item, replied in waiting:
                unanswered.append(make_row(thread, item, replied))
            for item, replied in done:
                answered.append(make_row(thread, item, replied))
            continue
        if waiting:
            waiting.sort(key=lambda pair: when_of(pair[0]), reverse=True)
            item, replied = waiting[0]
            unanswered.append(make_row(thread, item, replied))
        elif done:
            done.sort(key=lambda pair: when_of(pair[0]), reverse=True)
            item, replied = done[0]
            answered.append(make_row(thread, item, replied))
    unanswered.sort(key=lambda row: row["_when"], reverse=True)
    answered.sort(key=lambda row: row["_when"], reverse=True)
    return unanswered, answered


def greeting_status(row):
    return "Replied" if row.get("replied") else "Not replied yet"


def greeting_lines(row, now=None):
    # `now` is the report window end. Reply ages use the actual UTC clock so a
    # stale --now cannot show a week-old post as minutes ago. What's new does
    # the same from catch_lines.
    del now
    when = ""
    if row.get("created_at"):
        when = ago(ts(row["created_at"]), utc_now())
    kind = "Quote" if row.get("kind") == "quote" else "Reply"
    name = f"**[{row['name']}](https://x.com/{row['username']})**{person_marks(row)}"
    text = row.get("text") or "Post"
    url = (row.get("url") or "").strip()
    preview = f"[“{text}”]({url})" if url else f"“{text}”"
    if when:
        preview += f" · {when}"
    lines = [hard_break(f"{name} · {kind} · {greeting_status(row)}"), preview]
    if row.get("on_text"):
        lines[-1] = hard_break(lines[-1])
        lines.append(f"on “{row['on_text']}”")
    lines.append("")
    return lines


def public_greeting(row, now):
    data = {key: value for key, value in row.items() if key != "_when"}
    data["status"] = greeting_status(row)
    data["markdown"] = "\n".join(greeting_lines(row, now)).strip()
    return data


def unfollow_zone(name):
    text = (name or "").strip() or "Australia/Adelaide"
    return owner_zone(text)


def snapshot_local_date(day_str, zone):
    day = date.fromisoformat(day_str)
    return datetime(day.year, day.month, day.day, 12, tzinfo=zone).date()


def unfollow_lines(snapshot_dir, zone_name, since, now):
    """Who left between follower snapshots, grouped by the later file's local date."""
    if not snapshot_dir:
        return None
    zone = unfollow_zone(zone_name)
    import followers_welcome as fw
    snaps = sorted(fw.snapshots(snapshot_dir), key=lambda pair: pair[0])
    start = since.astimezone(zone).date()
    end = now.astimezone(zone).date()
    grouped = {}
    for (_day, users), (nxt_day, nxt) in zip(snaps, snaps[1:]):
        local = snapshot_local_date(nxt_day, zone)
        if local < start or local > end:
            continue
        names = []
        for uid in fw.ids_gone(users, nxt):
            handle = str((users.get(uid) or {}).get("username") or "").lstrip("@")
            if handle:
                names.append(handle)
        if names:
            grouped.setdefault(local, []).extend(names)
    label = getattr(zone, "key", None) or "Australia/Adelaide"
    lines = [f"### Unfollows ({label})", ""]
    if not grouped:
        lines.append("No unfollows in this stretch.")
        return lines
    for day in sorted(grouped):
        shown = ", ".join(f"@{name}" for name in sorted(set(grouped[day]), key=str.lower))
        lines.append(f"{day.day} {day.strftime('%b')}: {shown}")
    return lines


def write_chat_report(activity, path, handle, since, now, label, top=10, items_per_user=3,
                      show=CHAT_SHOW, window="day", waiting_rule=None, snapshot_line=None,
                      counts_lines=None, gone_lines=None):
    totals = activity["totals"]
    people = activity["threads"] + activity["interactions"] + activity["catch_up"]
    legend = []
    if any(person["stranger"] for person in people):
        legend.append("👤? = you don't follow them yet")
    if activity.get("has_good_neighbours"):
        legend.append("⭐ = good neighbour")
    counts = count_line(totals)
    lines = [hard_break(period_line(window, since, now, label))]
    lines.append(hard_break(counts) if legend else counts)
    if legend:
        lines.append(" · ".join(legend))
    if snapshot_line:
        lines.append(snapshot_line)
    lines.append("")
    flowers = activity["interactions"]
    if not flowers:
        lines.append("No replies, likes, or reposts in this stretch.")
    else:
        lines.append("### 💐 Flowers")
        lines.append("")
        for row in flowers[:show]:
            lines.extend(flower_lines(row))
        extra = len(flowers) - show
        if extra > 0:
            lines.append(f"+{extra} more in Flowers. Ask to see them.")
            lines.append("")
    catch = activity["catch_up"]
    if not catch:
        if activity.get("all_caught_up"):
            lines.append("You're all caught up with your good neighbours 🎉")
        elif activity.get("catch_hint"):
            lines.append("No one to catch up with in this stretch.")
            lines.append("Add people to your good neighbours list to keep up with them here.")
        else:
            lines.append("No recent original posts from your good neighbours.")
    else:
        lines.append("### 🏡 What's new")
        if activity.get("catch_hint"):
            lines.append("Add people to your good neighbours list to keep up with them here.")
        lines.append("")
        for person in catch[:show]:
            lines.extend(catch_lines(person))
        extra = len(catch) - show
        if extra > 0:
            lines.append(f"+{extra} more in What's new. Ask to see them.")
            lines.append("")
    unanswered, answered = greeting_rows(
        activity["threads"], waiting_rule,
        owner_replied_in=activity.get("owner_replied_in"))
    if not unanswered and not answered:
        lines.append("No replies or quote posts in this stretch.")
    else:
        lines.append("### 👋 Replies")
        lines.append("")
        if not unanswered:
            lines.append("No replies waiting on you.")
            lines.append("")
        for row in unanswered[:show]:
            lines.extend(greeting_lines(row, now))
        extra = len(unanswered) - show
        if extra > 0:
            lines.append(f"+{extra} more replies. Ask to see them.")
            lines.append("")
        if answered:
            lines.append(f"{len(answered)} already answered. Ask to see them.")
            lines.append("")
    if counts_lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(counts_lines)
    if gone_lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(gone_lines)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    return {
        "flowers": [flower_record(row) for row in flowers[show:]],
        "catch_up": [catch_record(person) for person in catch[show:]],
        "greetings_unanswered": [public_greeting(row, now) for row in unanswered[show:]],
        "greetings_answered": [public_greeting(row, now) for row in answered],
    }


def load_open_status(path):
    previous = {}
    if not path or not os.path.isfile(path):
        return previous
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                kind = (row.get("kind") or "").strip()
                handle = (row.get("handle") or "").strip().lstrip("@")
                status = (row.get("status") or "").strip().lower()
                if kind and handle and status in OPEN_ITEM_KEEP:
                    previous[(kind, handle.lower())] = status
    except (OSError, csv.Error, UnicodeDecodeError):
        return previous
    return previous


def open_item_rows(data, activity, thanks, handle, waiting_rule=None):
    rows = []
    seen = set()

    def add(kind, who, reason, link):
        who = (who or "").lstrip("@")
        if not who:
            return
        key = (kind, who.lower())
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "kind": kind,
            "handle": who,
            "reason": reason,
            "link": link or "",
            "status": "open",
        })

    for row in activity.get("interactions") or []:
        uid = row.get("id")
        if uid in data.followers and uid not in data.following:
            add(
                "follow_back",
                row.get("username"),
                "Follows you; you don't follow them",
                f"https://x.com/{row.get('username')}",
            )
    unanswered, _answered = greeting_rows(
        activity.get("threads") or [], waiting_rule,
        owner_replied_in=activity.get("owner_replied_in"))
    for row in unanswered:
        add(
            "first_reply",
            row.get("username"),
            "Waiting on a first reply" + (f": {row['text']}" if row.get("text") else ""),
            row.get("url") or f"https://x.com/{row.get('username')}",
        )
    for person in activity.get("catch_up") or []:
        latest = person.get("latest") or {}
        preview = snippet(latest.get("text")) if latest else ""
        add(
            "catch_up",
            person.get("username"),
            "Latest original post to catch up on" + (f": {preview}" if preview else ""),
            person.get("latest_url") or "",
        )
    if thanks:
        add("thank_you", handle, "Post the thank-you", thanks.get("intent") or "")
    return rows


def write_open_items(path, rows, previous=None):
    previous = previous or {}
    out = []
    for row in rows:
        key = (row["kind"], row["handle"].lower())
        if key in previous:
            row = dict(row, status=previous[key])
        out.append(row)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OPEN_ITEM_FIELDS))
        writer.writeheader()
        writer.writerows(out)
    return out


def write_thanks(path, thanks):
    if not thanks:
        text = "No neighbours to thank in this stretch.\n"
    else:
        text = (
            f"{thanks['text']}\n\n"
            f"[Post your thank-you]({thanks['intent']})\n\n"
            f"{thanks.get('note') or THANK_YOU_NOTE}\n"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def wrap_text(draw, text, face, width):
    words = (text or "").split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        trial = word if not current else current + " " + word
        if draw.textlength(trial, font=face) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_report(activity, path, handle, since, now, label, top=10, thanks=None,
                  picture_path=None):
    from PIL import Image, ImageDraw
    width = 1100
    margin = 36
    inner = width - margin * 2
    ink = (17, 24, 39)
    muted = (100, 116, 139)
    line = (226, 232, 240)
    blue = (37, 99, 235)
    purple = (109, 40, 217)
    rose = (190, 18, 60)
    green = (4, 120, 87)
    orange = (194, 65, 12)
    ok = (21, 128, 61)
    title_font = font(34)
    sub_font = font(16, bold=False)
    section_font = font(22)
    name_font = font(18)
    body_font = font(15, bold=False)
    small_font = font(13, bold=False)
    stat_font = font(26)
    stat_label = font(13, bold=False)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    blocks = []

    def add(height, painter):
        blocks.append((height, painter))

    def header(_draw, _img, y):
        _draw.text((margin, y), f"X Neighbours  ·  @{handle}", font=title_font, fill=ink)
        end = now.astimezone(timezone.utc)
        start = since.astimezone(timezone.utc)
        _draw.text((margin, y + 46),
                   f"{label}  ·  {start.strftime('%b %-d')} – {end.strftime('%b %-d')}",
                   font=sub_font, fill=muted)

    add(84, header)
    totals = activity["totals"]
    stats = [
        (str(totals["replies"]), "Replies", blue),
        (str(totals["quotes"]), "Quote posts", purple),
        (str(totals["likes"]), "Likes", rose),
        (str(totals["reposts"]), "Reposts", green),
    ]

    def stat_row(_draw, _img, y):
        gap = 14
        box_w = (inner - gap * 3) / 4
        for index, (value, caption, color) in enumerate(stats):
            x = margin + index * (box_w + gap)
            _draw.rounded_rectangle((x, y, x + box_w, y + 78), radius=14, fill=(255, 255, 255),
                                    outline=line, width=2)
            _draw.text((x + 16, y + 10), value, font=stat_font, fill=color)
            _draw.text((x + 16, y + 46), caption, font=stat_label, fill=muted)

    add(96, stat_row)

    def legend(_draw, _img, y):
        _draw.rounded_rectangle((margin, y, margin + 22, y + 22), radius=6, fill=(255, 247, 237),
                                outline=orange, width=2)
        _draw.text((margin + 6, y + 2), "?", font=small_font, fill=orange)
        caption = "you don't follow them yet"
        if activity.get("has_good_neighbours"):
            caption += "      ★ = good neighbour"
        _draw.text((margin + 30, y + 2), caption, font=body_font, fill=muted)

    add(40, legend)

    def draw_name(_draw, x, y, person, face):
        _draw.text((x, y), person["name"], font=face, fill=ink)
        used = dummy.textlength(person["name"], font=face)
        if person.get("good"):
            _draw.text((x + used + 6, y), "★", font=face, fill=(180, 83, 9))
            used += 6 + dummy.textlength("★", font=face)
        return x + used

    def section(title, color):
        def paint(_draw, _img, y, title=title, color=color):
            _draw.rounded_rectangle((margin, y, width - margin, y + 42), radius=10, fill=color)
            _draw.text((margin + 16, y + 8), title, font=section_font, fill=(255, 255, 255))
        add(56, paint)

    section("Flowers", rose)

    def table_head(_draw, _img, y):
        _draw.rectangle((margin, y, width - margin, y + 36), fill=(248, 250, 252))
        columns = [(margin + 16, "User"), (inner * 0.58 + margin, "Replies"),
                   (inner * 0.72 + margin, "Likes"), (inner * 0.86 + margin, "Reposts")]
        for x, caption in columns:
            _draw.text((x, y + 8), caption, font=small_font, fill=muted)

    add(36, table_head)
    table_rows = activity["interactions"][:top]
    if not table_rows:
        def empty_table(_draw, _img, y):
            _draw.text((margin + 16, y + 8), "No replies, likes, or reposts in this window.",
                       font=body_font, fill=muted)
        add(36, empty_table)
    for index, row in enumerate(table_rows):
        def paint_row(_draw, img, y, row=row, index=index):
            fill = (255, 255, 255) if index % 2 == 0 else (248, 250, 252)
            _draw.rectangle((margin, y, width - margin, y + 48), fill=fill)
            avatar = fetch_avatar(row["user"], os.path.join(os.path.dirname(path), ".avatar_cache"), 28)
            img.paste(avatar, (margin + 12, y + 10), avatar)
            draw_name(_draw, margin + 48, y + 6, row, name_font)
            handle_text = "@" + row["username"]
            _draw.text((margin + 48, y + 26), handle_text, font=small_font, fill=muted)
            if row["stranger"]:
                mark_x = margin + 48 + dummy.textlength(handle_text + " ", font=small_font)
                _draw.text((mark_x, y + 24), "?", font=small_font, fill=orange)
            _draw.text((inner * 0.58 + margin, y + 14), str(row["replies"]), font=body_font, fill=ink)
            _draw.text((inner * 0.72 + margin, y + 14), str(row["likes"]), font=body_font, fill=ink)
            _draw.text((inner * 0.86 + margin, y + 14), str(row["reposts"]), font=body_font, fill=ink)
        add(48, paint_row)

    section("What's new", green)

    def catch_intro(_draw, _img, y):
        if activity.get("has_good_neighbours"):
            text = "Latest original posts from your good neighbours"
        else:
            text = "Add people to your good neighbours list to keep up with them here."
        _draw.text((margin, y), text, font=body_font, fill=muted)

    add(28, catch_intro)
    if not activity["catch_up"]:
        def empty_catch(_draw, _img, y):
            if activity.get("all_caught_up"):
                text = "You're all caught up with your good neighbours."
            elif activity.get("has_good_neighbours"):
                text = "No recent original posts from your good neighbours."
            else:
                text = "No one to catch up with in this window."
            _draw.text((margin, y), text, font=body_font, fill=muted)
        add(28, empty_catch)
    for person in activity["catch_up"]:
        latest = person["latest"]
        text = snippet(latest.get("text")) if latest else "No latest post saved."
        lines = wrap_text(dummy, text, body_font, inner - 70)
        block_h = 36 + 22 * len(lines) + 8

        def paint_catch(_draw, img, y, person=person, lines=lines, latest=latest):
            avatar = fetch_avatar(person["user"], os.path.join(os.path.dirname(path), ".avatar_cache"), 36)
            img.paste(avatar, (margin, y), avatar)
            handle_x = draw_name(_draw, margin + 48, y, person, name_font) + 8
            _draw.text((handle_x, y + 2), "@" + person["username"], font=body_font, fill=muted)
            if person["stranger"]:
                mark_x = handle_x + dummy.textlength("@" + person["username"] + " ", font=body_font)
                _draw.text((mark_x, y + 2), "?", font=small_font, fill=orange)
            text_y = y + 28
            for row in lines:
                _draw.text((margin + 48, text_y), row, font=body_font, fill=ink)
                text_y += 22
            if latest and latest.get("created_at"):
                when = ago(ts(latest["created_at"]), utc_now())
                _draw.text((margin + 48, text_y), when + "   ·   Read post", font=small_font, fill=blue)
        add(block_h + 18, paint_catch)

    section("Replies", blue)
    if not activity["threads"]:
        def empty_threads(_draw, _img, y):
            _draw.text((margin, y), "No replies or quote posts in this window.", font=body_font, fill=muted)
        add(32, empty_threads)
    for thread in activity["threads"]:
        def paint_user(_draw, img, y, thread=thread):
            avatar = fetch_avatar(thread["user"], os.path.join(os.path.dirname(path), ".avatar_cache"), 42)
            img.paste(avatar, (margin, y), avatar)
            handle_x = draw_name(_draw, margin + 54, y, thread, name_font) + 8
            _draw.text((handle_x, y + 2), "@" + thread["username"], font=body_font, fill=muted)
            if thread["stranger"]:
                mark_x = handle_x + dummy.textlength("@" + thread["username"] + "   ", font=body_font)
                _draw.rounded_rectangle((mark_x, y + 2, mark_x + 28, y + 22), radius=6,
                                        fill=(255, 247, 237), outline=orange, width=1)
                _draw.text((mark_x + 8, y + 2), "?", font=small_font, fill=orange)
        add(52, paint_user)
        for item in thread["items"]:
            kind = "Reply" if item["kind"] == "reply" else "Quote"
            kind_color = blue if item["kind"] == "reply" else purple
            state = "Replied" if item["replied"] else "Not replied yet"
            state_color = ok if item["replied"] else orange
            body_lines = wrap_text(dummy, item["text"], body_font, inner - 150)
            on_lines = wrap_text(dummy, "on your post: “" + item["on_text"] + "”", small_font, inner - 80)
            block_h = 36 + 22 * len(body_lines) + 20 * len(on_lines) + 36

            def paint_item(_draw, _img, y, item=item, kind=kind, kind_color=kind_color,
                           state=state, state_color=state_color, body_lines=body_lines, on_lines=on_lines):
                _draw.rounded_rectangle((margin + 54, y, width - margin, y + block_h - 8),
                                        radius=10, fill=(255, 255, 255), outline=line, width=1)
                _draw.rounded_rectangle((margin + 66, y + 10, margin + 66 + dummy.textlength(kind, font=small_font) + 16, y + 30),
                                        radius=6, fill=kind_color)
                _draw.text((margin + 74, y + 11), kind, font=small_font, fill=(255, 255, 255))
                text_y = y + 36
                for row in body_lines:
                    _draw.text((margin + 66, text_y), row, font=body_font, fill=ink)
                    text_y += 22
                for row in on_lines:
                    _draw.text((margin + 78, text_y), row, font=small_font, fill=muted)
                    text_y += 20
                meta = f"{ago(item['created_at'], utc_now())}   ·   {state}"
                _draw.text((margin + 66, text_y), meta, font=small_font, fill=state_color)
            add(block_h, paint_item)

    section("Thank your neighbours on X", (180, 83, 9))
    thank_text = (thanks or {}).get("text") or "Thank you to my X neighbours."
    thumb = None
    if picture_path and os.path.isfile(picture_path):
        with Image.open(picture_path) as src:
            thumb = src.convert("RGB")
            thumb.thumbnail((220, 220))
    text_width = inner - (thumb.width + 24 if thumb else 0)
    thank_lines = wrap_text(dummy, thank_text, body_font, text_width)
    thank_h = max(thumb.height + 16 if thumb else 0, 22 * len(thank_lines) + 36)

    def paint_thanks(_draw, img, y, thank_lines=thank_lines, thumb=thumb):
        text_x = margin
        if thumb is not None:
            img.paste(thumb, (margin, y))
            text_x = margin + thumb.width + 16
        text_y = y
        for row in thank_lines:
            _draw.text((text_x, text_y), row, font=body_font, fill=ink)
            text_y += 22
        _draw.text((text_x, text_y + 8), "Post your thank-you", font=small_font, fill=blue)

    add(thank_h, paint_thanks)

    height = 28 + sum(block[0] for block in blocks) + 24
    image = Image.new("RGB", (width, height), (241, 245, 249))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=18, fill=(255, 255, 255))
    y = 32
    for block_h, painter in blocks:
        painter(draw, image, y)
        y += block_h
    image.save(path, optimize=True)


WEEKLY_FEEDBACK = (
    "Anything you'd change about X Neighbours? "
    "Tell me and I'll turn it into a feedback link."
)


def thank_you_post(when, zone):
    """Thank-you text with the report date. It never names anyone."""
    local = when.astimezone(zone)
    day = local.strftime("%B %-d")
    text = (
        f"My X neighbours circle, {day}! Thank you all for being here "
        "and taking the time to reply to my posts. I really appreciate it!"
    )
    return text, "https://x.com/intent/post?text=" + urllib.parse.quote(text)


def extra_sections(data, since, now, handle, top, like_weight, reply_weight, per_post, per_day,
                   repost_weight=None, quote_weight=None, mutual_bonus=0,
                   new_follower_credit=0):
    lines = []
    if per_day:
        lines += ["### Per day", ""]
        for day, day_start, day_end in each_utc_day(since, now):
            activity = build_activity(
                data, day_start, day_end, top, like_weight, reply_weight, "both", now,
                mutual_bonus=mutual_bonus, new_follower_credit=new_follower_credit,
                repost_weight=repost_weight, quote_weight=quote_weight)
            totals = activity["totals"]
            lines.append(
                f"- **{day.isoformat()}** — {totals['replies']} replies, {totals['quotes']} quotes, "
                f"{totals['likes']} likes, {totals['reposts']} reposts"
            )
        lines.append("")
    if per_post:
        lines += ["### Per post", ""]
        posts = []
        for pid, post in data.posts.items():
            if data._in_window(post.get("created_at"), since, now):
                posts.append((ts(post["created_at"]), pid, post))
        posts.sort(reverse=True)
        if not posts:
            lines.append("No posts in this window.")
        for when, pid, post in posts:
            likes = sum(1 for uid in data.likes.get(pid, []) if not hidden_user(data, uid))
            reposts = sum(1 for uid in data.reposts.get(pid, []) if not hidden_user(data, uid))
            text = snippet(post.get("text")) or "Post"
            lines.append(
                f"- {when.strftime('%Y-%m-%d %H:%M UTC')} — {text} — "
                f"{likes} likes, {reposts} reposts — https://x.com/{handle}/status/{pid}"
            )
        lines.append("")
    return "\n".join(lines)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Build an X Neighbours report, and sometimes the neighbours picture."
    )
    parser.add_argument("--version", action="version", version=xn_version.version())
    parser.add_argument("--config", default=os.path.join(here, "config.json"))
    parser.add_argument("--handle")
    parser.add_argument("--user-id", dest="user_id")
    parser.add_argument("--data", default=os.path.join(here, "data"))
    parser.add_argument(
        "--snapshot-dir", dest="snapshot_dir",
        help="Echo daily snapshot folder (YYYY-MM-DD.json followers, following/YYYY-MM-DD.json)")
    parser.add_argument(
        "--backfill-counts", action="store_true",
        help="add missing counts.csv rows from follower snapshots, then exit")
    parser.add_argument("--out", default=here)
    parser.add_argument("--now", help="UTC ISO time the data was fetched (default: now)")
    parser.add_argument("--like-weight", dest="like_weight", type=float)
    parser.add_argument("--reply-weight", dest="reply_weight", type=float)
    parser.add_argument("--repost-weight", dest="repost_weight", type=float)
    parser.add_argument("--quote-weight", dest="quote_weight", type=float)
    parser.add_argument("--exclude", help="comma-separated handles to drop; '' for none")
    parser.add_argument("--rings", type=int)
    parser.add_argument("--top", type=int)
    parser.add_argument("--theme", choices=sorted(THEMES))
    parser.add_argument("--windows", help="comma-separated: day,week")
    parser.add_argument("--report", choices=REPORTS,
                        help="likes, replies, or both (default both)")
    parser.add_argument("--per-post", dest="per_post", action=argparse.BooleanOptionalAction,
                        default=None, help="add a per-post breakdown")
    parser.add_argument("--per-day", dest="per_day", action=argparse.BooleanOptionalAction,
                        default=None, help="add a per-day breakdown to the markdown")
    parser.add_argument("--days", type=int, help="custom period length in days")
    parser.add_argument("--since", help="UTC ISO start for a fresh report since the last one")
    parser.add_argument("--state", help="path to state.json in the bot store")
    parser.add_argument("--timezone", help="IANA timezone for the thank-you date, such as Europe/Stockholm")
    parser.add_argument("--fresh-hours", dest="fresh_hours", type=float,
                        help="owner override for how many hours a report stays fresh")
    parser.add_argument("--first-run", action="store_true",
                        help="report the last first_run_days and always draw the picture")
    parser.add_argument("--report-card", action="store_true",
                        help="also write the report card PNG (off by default)")
    parser.add_argument("--picture", dest="picture", action=argparse.BooleanOptionalAction,
                        default=None, help="include the neighbours picture (default: every report)")
    parser.add_argument("--show-names", dest="show_names", action="store_true", default=None)
    parser.add_argument("--hide-names", dest="show_names", action="store_false")
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--good-neighbours", dest="good_neighbours",
                        help="path to the personal good neighbours JSON; a missing file is an empty list")
    args = parser.parse_args()
    if args.backfill_counts:
        folder = args.snapshot_dir
        if not folder or not os.path.isdir(folder):
            parser.error("--backfill-counts needs --snapshot-dir pointing at a snapshot folder")
        backfill_counts(folder)
        return
    cfg = dict(DEFAULTS)
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            parser.error(f"{args.config} must contain a JSON object")
        apply_loaded_config(cfg, loaded)
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = as_list(value) if key in ("exclude", "windows") else value
    cfg["exclude"] = [item.lstrip("@") for item in as_list(cfg.get("exclude"))]
    cfg["windows"] = as_list(cfg.get("windows"))
    if not cfg["handle"] or not cfg["user_id"]:
        parser.error("handle and user_id are required (config.json or --handle/--user-id)")
    cfg["handle"] = str(cfg["handle"]).lstrip("@")
    unknown = [name for name in cfg["windows"] if name not in WINDOWS]
    if unknown:
        parser.error("windows must be day, week, or both")
    rings = int(cfg["rings"])
    if not 1 <= rings <= 4:
        parser.error("rings must be from 1 to 4")
    cfg["rings"] = rings
    top = int(cfg["top"])
    if top < 1:
        parser.error("top must be at least 1")
    cfg["top"] = top
    if cfg["theme"] not in THEMES:
        parser.error("theme must be midnight, light, or sunset")
    cfg["show_names"] = as_bool(cfg["show_names"])
    cfg["per_post"] = as_bool(cfg.get("per_post"))
    cfg["per_day"] = as_bool(cfg.get("per_day"))
    if cfg.get("report") not in REPORTS:
        parser.error("report must be likes, replies, or both")
    needs_pillow = args.report_card or args.picture is not False
    if needs_pillow and not args.no_image:
        try:
            import PIL  # noqa: F401
        except ImportError:
            sys.exit("Pillow is required to draw the picture. "
                     "Install it with: python3 -m pip install -r requirements.txt")
    if not os.path.isdir(args.data):
        parser.error(f"data folder not found: {args.data}")
    snapshot_dir = cfg.get("snapshot_dir") or None
    if snapshot_dir and not os.path.isdir(snapshot_dir):
        parser.error(f"snapshot folder not found: {snapshot_dir}")
    os.makedirs(args.out, exist_ok=True)
    weights = resolve_weights(cfg)
    like_weight = weights["like_weight"]
    reply_weight = weights["reply_weight"]
    repost_weight = weights["repost_weight"]
    quote_weight = weights["quote_weight"]
    mutual_bonus = weights["mutual_bonus"]
    new_follower_credit = weights["new_follower_credit"]
    thread_reply_factor = weights["thread_reply_factor"]
    author_engaged_bonus = weights["author_engaged_bonus"]
    waiting_rule = waiting_rule_from(cfg.get("waiting_rule"))
    me_id = str(cfg["user_id"])
    now = ts(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    data = Data(args.data, me_id, snapshot_dir=snapshot_dir)
    data.snapshot_folder = snapshot_dir
    data.exclude = {handle.lower() for handle in cfg["exclude"]}
    neighbours = load_good_neighbours(cfg.get("good_neighbours"))
    me = {"id": me_id, "username": cfg["handle"]}
    stored = data.users.get(me_id, {})
    me.update({k: v for k, v in stored.items() if v not in (None, "")})
    output = {}
    mode = cfg["report"]
    if args.since:
        since_at = ts(args.since)
        if since_at.tzinfo is None:
            since_at = since_at.replace(tzinfo=timezone.utc)
        if since_at >= now:
            parser.error("--since must be earlier than --now")
        windows = [("fresh", since_at, "Since your last report")]
    elif args.first_run:
        first_days = int(cfg.get("first_run_days") or 2)
        if first_days < 1:
            parser.error("first_run_days must be at least 1")
        first_label = "Last 24 hours" if first_days == 1 else f"Last {first_days} days"
        windows = [("first", first_days, first_label)]
        if args.picture is None:
            args.picture = True
    elif args.days:
        if args.days < 1:
            parser.error("days must be at least 1")
        custom_label = "Last 24 hours" if args.days == 1 else f"Last {args.days} days"
        windows = [("custom", args.days, custom_label)]
    else:
        windows = [
            (key, days, label) for key, (label, days, _subtitle) in (
                (name, WINDOWS[name]) for name in cfg["windows"]
            )
        ]
    zone = owner_zone(cfg.get("timezone"))
    data.score_zone = zone
    for key, span, label in windows:
        since = span if isinstance(span, datetime) else now - timedelta(days=span)
        activity = build_activity(
            data, since, now, cfg["top"], like_weight, reply_weight, mode, now,
            good_neighbours=neighbours,
            mutual_bonus=mutual_bonus, new_follower_credit=new_follower_credit,
            repost_weight=repost_weight, quote_weight=quote_weight,
            thread_reply_factor=thread_reply_factor,
            author_engaged_bonus=author_engaged_bonus)
        md_path = os.path.join(args.out, f"report_{key}.md")
        png_path = os.path.join(args.out, f"report_{key}.png")
        text, intent = thank_you_post(now, zone)
        thanks = {
            "text": text,
            "intent": intent,
            "link_label": "Post your thank-you",
            "note": THANK_YOU_NOTE,
        }
        draw_picture = args.picture is not False and not args.no_image
        picture_path = None
        if draw_picture:
            picture_path = os.path.join(args.out, f"x_neighbours_{key}.png")
            draw_circle(
                data, me, activity["ranked"], picture_path,
                rings=cfg["rings"], show_names=cfg["show_names"],
                subtitle=SUBTITLES["both"].get(key, f"Top neighbours, {label.lower()}"),
                theme=cfg["theme"])
        thanks_path = os.path.join(args.out, f"report_{key}_thanks.md")
        more_path = os.path.join(args.out, f"report_{key}_more.json")
        snapshot_line = None
        counts_lines = None
        if data.snapshot_date:
            snapshot_line = snapshot_age_line(
                data.snapshot_date, now,
                following_missing=data.snapshot_following_missing)
        gone_lines = None
        if snapshot_dir:
            counts_lines = counts_section_lines(
                load_counts_csv(os.path.join(snapshot_dir, "counts.csv")))
            gone_lines = unfollow_lines(snapshot_dir, cfg.get("timezone"), since, now)
        more = write_chat_report(
            activity, md_path, cfg["handle"], since, now, label,
            top=cfg["top"], items_per_user=int(cfg.get("items_per_user") or 3),
            window=key, waiting_rule=waiting_rule, snapshot_line=snapshot_line,
            counts_lines=counts_lines, gone_lines=gone_lines)
        with open(more_path, "w", encoding="utf-8") as fh:
            json.dump(more, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        write_thanks(thanks_path, thanks)
        open_path = os.path.join(args.out, "open_items.csv")
        write_open_items(
            open_path,
            open_item_rows(data, activity, thanks, cfg["handle"], waiting_rule),
            load_open_status(open_path),
        )
        if key == "week":
            with open(thanks_path, "a", encoding="utf-8") as fh:
                fh.write("\n" + WEEKLY_FEEDBACK + "\n")
        if cfg["per_post"] or cfg["per_day"]:
            with open(md_path, "a", encoding="utf-8") as fh:
                fh.write("\n" + extra_sections(
                    data, since, now, cfg["handle"], cfg["top"], like_weight, reply_weight,
                    cfg["per_post"], cfg["per_day"],
                    repost_weight=repost_weight, quote_weight=quote_weight,
                    mutual_bonus=mutual_bonus,
                    new_follower_credit=new_follower_credit) + "\n")
        if args.report_card and not args.no_image:
            render_report(
                activity, png_path, cfg["handle"], since, now, label,
                top=cfg["top"], thanks=thanks, picture_path=picture_path)
        output[key] = {
            "replies": activity["totals"]["replies"],
            "quotes": activity["totals"]["quotes"],
            "likes": activity["totals"]["likes"],
            "reposts": activity["totals"]["reposts"],
            "catch_up": ["@" + person["username"] for person in activity["catch_up"]],
            "since": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "report_md": md_path,
            "thanks_md": thanks_path,
            "more_json": more_path,
            "open_items": open_path,
            "more_counts": {name: len(rows) for name, rows in more.items()},
            "thank_you": thanks,
            "messages": [
                {"part": "sections_1_to_3", "text_file": md_path, "images": []},
                {
                    "part": "section_4",
                    "text_file": thanks_path,
                    "text": thanks["text"],
                    "link_label": "Post your thank-you",
                    "intent": thanks["intent"],
                    "images": [picture_path] if picture_path else [],
                },
            ],
        }
        if cfg.get("state"):
            import report_state
            report_state.record(
                cfg["state"], at=now, since=since, until=now,
                replies=activity["totals"]["replies"],
                quotes=activity["totals"]["quotes"],
                likes=activity["totals"]["likes"],
                reposts=activity["totals"]["reposts"],
                override=cfg.get("fresh_hours"))
        if key == "week":
            output[key]["feedback_line"] = WEEKLY_FEEDBACK
        else:
            suggestions = suggest_good_neighbours(
                activity, neighbours, top=cfg["top"])
            if suggestions:
                output[key]["suggest_good_neighbours"] = suggestions
                output[key]["add_question"] = {
                    "label": "⭐ Add",
                    "prompt": "Add to good neighbours?",
                    "options": [person["label"] for person in suggestions],
                    "free_text": "Or name anyone else",
                }
        if args.report_card and not args.no_image:
            output[key]["report_png"] = png_path
        if picture_path:
            output[key]["picture"] = picture_path
        if data.snapshot_date:
            output[key]["snapshot_date"] = data.snapshot_date
            output[key]["follower_snapshot_age_days"] = snapshot_age_days(
                data.snapshot_date, now)
            if data.snapshot_following_missing:
                output[key]["following_snapshot_missing"] = True
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
