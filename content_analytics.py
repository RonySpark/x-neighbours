#!/usr/bin/env python3
"""Content analytics from X Analytics CSV exports. No X API calls.

Reads content_*.csv and overview_*.csv from an exports folder. Post time is
not in the CSV; it is decoded from the Post id snowflake. Video exports are
ignored.

Usage:
  python3 content_analytics.py --exports <dir> --out <dir> [--json] [--data <dir>]
  python3 content_analytics.py --exports <dir> --post-follows-csv PATH [--review-log PATH]
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import x_neighbours as xn


TWITTER_EPOCH_MS = 1288834974657
ADELAIDE = ZoneInfo("Australia/Adelaide")
DEFAULT_KEYWORDS = ["Tesla", "Adelaide"]
MIN_TYPE_POSTS = 3
SMALL_SAMPLE = 5
SHORT_MAX = 80
SNAPSHOT_STALE_DAYS = 7
MEDIUM_MAX = 200

CONTENT_COLUMNS = {
    "Post id": "post_id",
    "Date": "date_raw",
    "Post text": "text",
    "Post Link": "link",
    "Impressions": "impressions",
    "Likes": "likes",
    "Engagements": "engagements",
    "Bookmarks": "bookmarks",
    "Shares": "shares",
    "New follows": "new_follows",
    "Replies": "replies",
    "Reposts": "reposts",
    "Quotes": "quotes",
    "Profile visits": "profile_visits",
    "Detail Expands": "detail_expands",
    "URL Clicks": "url_clicks",
    "Hashtag Clicks": "hashtag_clicks",
    "Permalink Clicks": "permalink_clicks",
}
CONTENT_COUNTS = [
    "impressions", "likes", "engagements", "bookmarks", "shares", "new_follows",
    "replies", "reposts", "quotes", "profile_visits", "detail_expands",
    "url_clicks", "hashtag_clicks", "permalink_clicks",
]
OVERVIEW_COLUMNS = {
    "Date": "date_raw",
    "Impressions": "impressions",
    "Likes": "likes",
    "Engagements": "engagements",
    "Bookmarks": "bookmarks",
    "Shares": "shares",
    "New follows": "new_follows",
    "Unfollows": "unfollows",
    "Replies": "replies",
    "Reposts": "reposts",
    "Profile visits": "profile_visits",
    "Create Post": "create_post",
    "Video views": "video_views",
    "Media views": "media_views",
}
OVERVIEW_COUNTS = [
    "impressions", "likes", "engagements", "bookmarks", "shares", "new_follows",
    "unfollows", "replies", "reposts", "profile_visits", "create_post",
    "video_views", "media_views",
]


def parse_export_date(value):
    """Parse 'Mon, Nov 16, 2026' as a UTC calendar day."""
    if pd.isna(value):
        return pd.NaT
    text = str(value).strip()
    if not text:
        return pd.NaT
    return datetime.strptime(text, "%a, %b %d, %Y").replace(tzinfo=timezone.utc)


def snowflake_utc(post_id):
    """UTC time encoded in a post id. ms = (id >> 22) + 1288834974657."""
    ms = (int(post_id) >> 22) + TWITTER_EPOCH_MS
    return xn.ts(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def csv_paths(folder, prefix):
    """Newest export first so overlapping ids/days keep the freshest file."""
    paths = glob.glob(os.path.join(folder, f"{prefix}_*.csv"))
    return sorted(paths, key=lambda path: (os.path.getmtime(path), path), reverse=True)


def to_counts(frame, columns):
    for col in columns:
        if col not in frame.columns:
            frame[col] = 0
            continue
        values = frame[col].astype(str).str.replace(",", "", regex=False)
        frame[col] = pd.to_numeric(values, errors="coerce").fillna(0)
    return frame


def read_tables(paths, rename, counts):
    frames = []
    dtypes = {"Post id": str} if "Post id" in rename else None
    for path in paths:
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype=dtypes)
        frame = frame.rename(columns={key: rename[key] for key in rename if key in frame.columns})
        wanted = [col for col in rename.values() if col in frame.columns]
        frame = frame[wanted].copy()
        frame = to_counts(frame, [col for col in counts if col in frame.columns])
        if "date_raw" in frame.columns:
            when = frame["date_raw"].map(parse_export_date)
            frame["date"] = pd.to_datetime(when, utc=True).dt.date
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_content(folder):
    paths = csv_paths(folder, "content")
    if not paths:
        return pd.DataFrame(columns=list(CONTENT_COLUMNS.values()))
    posts = read_tables(paths, CONTENT_COLUMNS, CONTENT_COUNTS)
    if posts.empty:
        return posts
    posts["post_id"] = posts["post_id"].fillna("").astype(str).str.strip()
    posts = posts[posts["post_id"].str.fullmatch(r"[0-9]+", na=False)].copy()
    if posts.empty:
        return posts
    posts["text"] = posts["text"].fillna("").astype(str)
    posts["link"] = posts["link"].fillna("").astype(str)
    posts = posts.drop_duplicates(subset=["post_id"], keep="first")
    posted = posts["post_id"].map(snowflake_utc)
    posts["posted_at"] = posted
    adelaide = posted.map(lambda when: when.astimezone(ADELAIDE))
    posts["adelaide_at"] = adelaide
    posts["adelaide_date"] = adelaide.map(lambda when: when.date())
    posts["adelaide_hour"] = adelaide.map(lambda when: when.hour)
    posts["adelaide_weekday"] = adelaide.map(lambda when: when.strftime("%A"))
    impressions = posts["impressions"]
    engagements = posts["engagements"]
    posts["engagement_rate"] = (engagements / impressions).where(impressions > 0, 0.0)
    return posts


def load_overview(folder):
    paths = csv_paths(folder, "overview")
    if not paths:
        return pd.DataFrame(columns=list(OVERVIEW_COLUMNS.values()))
    daily = read_tables(paths, OVERVIEW_COLUMNS, OVERVIEW_COUNTS)
    if daily.empty:
        return daily
    daily = daily.dropna(subset=["date"])
    daily = daily.drop_duplicates(subset=["date"], keep="first")
    return daily.sort_values("date").reset_index(drop=True)


def load_exports(folder):
    """Load content and overview CSVs. Other files in the folder are left alone."""
    if not os.path.isdir(folder):
        raise SystemExit(f"exports folder not found: {folder}")
    content = load_content(folder)
    overview = load_overview(folder)
    if content.empty and overview.empty:
        raise SystemExit(f"no content_*.csv or overview_*.csv in {folder}")
    return {"content": content, "overview": overview}


def clip_text(text, n=72):
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 3] + "..."


def latest_export_date(overview, content):
    dates = []
    if overview is not None and not overview.empty:
        dates.append(max(overview["date"]))
    if content is not None and not content.empty:
        dates.append(max(content["date"]))
    if not dates:
        raise SystemExit("exports have no dates")
    return max(dates)


def week_windows(end):
    """This week is `end` and the six UTC days before it. Previous is the seven before that."""
    this_start = end - timedelta(days=6)
    prev_end = this_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    return (this_start, end), (prev_start, prev_end)


def in_days(frame, start, end, column="date"):
    if frame is None or frame.empty:
        return frame
    return frame[(frame[column] >= start) & (frame[column] <= end)].copy()


def sum_counts(frame, columns):
    if frame is None or frame.empty:
        return {col: 0 for col in columns}
    return {col: int(frame[col].sum()) if col in frame.columns else 0 for col in columns}


def change(this, previous):
    delta = this - previous
    pct = None if previous == 0 else delta / previous
    return {"this": this, "previous": previous, "change": delta, "pct": pct}


def post_ref(row):
    return {
        "post_id": str(row["post_id"]),
        "text": clip_text(row.get("text", "")),
        "link": str(row.get("link", "") or ""),
        "impressions": int(row["impressions"]),
        "engagements": int(row["engagements"]),
        "engagement_rate": float(row["engagement_rate"]),
        "new_follows": int(row["new_follows"]),
        "detail_expands": int(row.get("detail_expands", 0) or 0),
        "profile_visits": int(row.get("profile_visits", 0) or 0),
    }


def ranked_posts(posts, column, n=3):
    if posts is None or posts.empty:
        return [], []
    high = posts.sort_values(column, ascending=False)
    low = posts.sort_values(column, ascending=True)
    top = [post_ref(row) for _, row in high.head(n).iterrows()]
    bottom = [post_ref(row) for _, row in low.head(n).iterrows()]
    return top, bottom


def scorecard(overview, content):
    """Last 7 UTC days vs the 7 days before, plus top and bottom posts."""
    end = latest_export_date(overview, content)
    this_span, prev_span = week_windows(end)
    this_days = in_days(overview, *this_span)
    prev_days = in_days(overview, *prev_span)
    this_posts = in_days(content, *this_span)
    prev_posts = in_days(content, *prev_span)
    overview_keys = [
        "impressions", "likes", "engagements", "bookmarks", "shares",
        "new_follows", "unfollows", "replies", "reposts", "profile_visits",
        "create_post", "video_views", "media_views",
    ]
    this_tot = sum_counts(this_days, overview_keys)
    prev_tot = sum_counts(prev_days, overview_keys)
    totals = {key: change(this_tot[key], prev_tot[key]) for key in overview_keys}
    content_keys = ["detail_expands", "url_clicks", "hashtag_clicks", "permalink_clicks"]
    this_extra = sum_counts(this_posts, content_keys)
    prev_extra = sum_counts(prev_posts, content_keys)
    extra = {key: change(this_extra[key], prev_extra[key]) for key in content_keys}
    extra["posts"] = change(int(len(this_posts)), int(len(prev_posts)))
    by = {}
    for column in ("impressions", "engagement_rate", "new_follows"):
        top, bottom = ranked_posts(this_posts, column)
        by[column] = {"top": top, "bottom": bottom}
    return {
        "end": end.isoformat(),
        "this_week": [this_span[0].isoformat(), this_span[1].isoformat()],
        "previous_week": [prev_span[0].isoformat(), prev_span[1].isoformat()],
        "totals": totals,
        "from_content": extra,
        "posts": by,
    }


def load_keywords(config_path=None):
    if not config_path:
        return list(DEFAULT_KEYWORDS)
    with open(config_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    tags = cfg.get("keyword_tags") if isinstance(cfg, dict) else None
    if not tags:
        return list(DEFAULT_KEYWORDS)
    return [str(tag) for tag in tags if str(tag).strip()]


def classify_post(text, keywords=None):
    """Labels from the post text. Media is omitted: the CSV does not mark it."""
    text = text or ""
    stripped = text.lstrip()
    labels = []
    if re.search(r"https?://|t\.co", text, re.I):
        labels.append("link")
    if re.match(r"\d+\s*/", stripped) or "🧵" in text:
        labels.append("thread")
    if stripped.startswith("@"):
        labels.append("reply")
    else:
        labels.append("original")
    if "?" in text:
        labels.append("question")
    length = len(text)
    if length <= SHORT_MAX:
        labels.append("short")
    elif length <= MEDIUM_MAX:
        labels.append("medium")
    else:
        labels.append("long")
    lower = text.lower()
    for word in keywords or DEFAULT_KEYWORDS:
        if word and word.lower() in lower:
            labels.append(word)
    return labels


def content_types(posts, keywords=None, min_posts=MIN_TYPE_POSTS):
    """Median impressions and engagement rate per type, dropping types with few posts."""
    keywords = keywords or DEFAULT_KEYWORDS
    if posts is None or posts.empty:
        return []
    rows = []
    for _, post in posts.iterrows():
        for label in classify_post(post.get("text", ""), keywords):
            rows.append({
                "type": label,
                "impressions": post["impressions"],
                "engagement_rate": post["engagement_rate"],
            })
    if not rows:
        return []
    grouped = pd.DataFrame(rows).groupby("type")
    out = []
    for label, group in grouped:
        if len(group) < min_posts:
            continue
        out.append({
            "type": label,
            "posts": int(len(group)),
            "median_impressions": float(group["impressions"].median()),
            "median_engagement_rate": float(group["engagement_rate"].median()),
        })
    out.sort(key=lambda row: (-row["median_engagement_rate"], -row["median_impressions"], row["type"]))
    return out


WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def hour_label(start, span=3):
    return f"{start:02d}:00-{start + span - 1:02d}:00"


def _drop_loudest(posts):
    """Drop int(n * 0.05) posts. Zero when that is under 1, so a 3-post week is kept."""
    n_drop = int(len(posts) * 0.05)
    if n_drop < 1:
        return posts.copy(), 0, None
    ranked = posts.sort_values("impressions", ascending=False)
    dropped_rows = ranked.iloc[:n_drop]
    kept = ranked.iloc[n_drop:].copy()
    if kept.empty:
        return posts.copy(), 0, None
    return kept, int(len(dropped_rows)), float(dropped_rows["impressions"].min())


def _slot_posts(kept, weekday, block):
    if kept is None or kept.empty or (not weekday and not block):
        return 0
    mask = pd.Series(True, index=kept.index)
    if weekday:
        mask &= kept["adelaide_weekday"] == weekday["weekday"]
    if block:
        hours = {block["start_hour"], block["start_hour"] + 1, block["start_hour"] + 2}
        mask &= kept["adelaide_hour"].isin(hours)
    return int(mask.sum())


def _time_slots(kept):
    """Best weekday and 3-hour block. A block must start at an hour that has posts."""
    weekday_rows = []
    for name, group in kept.groupby("adelaide_weekday"):
        if len(group) < MIN_TYPE_POSTS:
            continue
        weekday_rows.append({
            "weekday": name,
            "posts": int(len(group)),
            "median_impressions": float(group["impressions"].median()),
            "median_engagement_rate": float(group["engagement_rate"].median()),
        })
    weekday_rows.sort(key=lambda row: (
        -row["median_impressions"], WEEKDAYS.index(row["weekday"])
        if row["weekday"] in WEEKDAYS else 99,
    ))
    posted_hours = {int(hour) for hour in kept["adelaide_hour"].tolist()}
    blocks = []
    for start in range(0, 22):
        if start not in posted_hours:
            continue
        hours = {start, start + 1, start + 2}
        group = kept[kept["adelaide_hour"].isin(hours)]
        if len(group) < MIN_TYPE_POSTS:
            continue
        blocks.append({
            "start_hour": start,
            "end_hour": start + 2,
            "label": hour_label(start),
            "posts": int(len(group)),
            "median_impressions": float(group["impressions"].median()),
            "median_engagement_rate": float(group["engagement_rate"].median()),
        })
    blocks.sort(key=lambda row: (-row["median_impressions"], row["start_hour"]))
    pivot = {}
    for name, group in kept.groupby("adelaide_weekday"):
        hours = {
            str(int(hour)): float(part["impressions"].median())
            for hour, part in group.groupby("adelaide_hour")
        }
        pivot[name] = hours
    weekday = weekday_rows[0] if weekday_rows else None
    block = blocks[0] if blocks else None
    return {
        "kept": int(len(kept)),
        "pivot": pivot,
        "best_weekday": weekday,
        "best_block": block,
        "sample_posts": _slot_posts(kept, weekday, block),
    }


def posting_times(posts, keywords=None):
    """Median impressions by Adelaide weekday and hour, after dropping the top 5%."""
    empty = {
        "kept": 0,
        "dropped": 0,
        "cutoff": None,
        "pivot": {},
        "best_weekday": None,
        "best_block": None,
        "sample_posts": 0,
        "by_type": [],
    }
    if posts is None or posts.empty:
        return empty
    kept, dropped, cutoff = _drop_loudest(posts)
    overall = _time_slots(kept)
    by_type = []
    keywords = keywords or DEFAULT_KEYWORDS
    tagged = [classify_post(text, keywords) for text in kept["text"].fillna("")]
    labels = sorted({label for tags in tagged for label in tags})
    for label in labels:
        subset = kept[[label in tags for tags in tagged]]
        if len(subset) < MIN_TYPE_POSTS:
            continue
        slot = _time_slots(subset)
        if not slot["best_weekday"] and not slot["best_block"]:
            continue
        by_type.append({
            "type": label,
            "kept": slot["kept"],
            "best_weekday": slot["best_weekday"],
            "best_block": slot["best_block"],
            "sample_posts": slot["sample_posts"],
        })
    by_type.sort(key=lambda row: (
        -((row["best_block"] or row["best_weekday"] or {}).get("median_impressions") or 0),
        row["type"],
    ))
    return {
        "kept": overall["kept"],
        "dropped": dropped,
        "cutoff": cutoff,
        "pivot": overall["pivot"],
        "best_weekday": overall["best_weekday"],
        "best_block": overall["best_block"],
        "sample_posts": overall["sample_posts"],
        "by_type": by_type,
    }


def _count_note(n):
    word = "post" if n == 1 else "posts"
    note = f"{n} {word}"
    if n < SMALL_SAMPLE:
        note += ", small sample"
    return f"({note})"


def best_time_phrase(times):
    """Combine weekday and block only when their overlap meets the 3-post floor."""
    weekday = times.get("best_weekday") or None
    block = times.get("best_block") or None
    if weekday and weekday.get("posts", 0) < MIN_TYPE_POSTS:
        weekday = None
    if block and block.get("posts", 0) < MIN_TYPE_POSTS:
        block = None
    if not weekday and not block:
        return None
    together = times.get("sample_posts") or 0
    if weekday and block:
        if together >= MIN_TYPE_POSTS:
            return (
                f"{weekday['weekday']} {block['label']} Adelaide {_count_note(together)}"
            )
        return (
            f"{weekday['weekday']} {_count_note(weekday['posts'])} or "
            f"{block['label']} {_count_note(block['posts'])}, "
            "not enough posts in both to combine"
        )
    if weekday:
        return f"{weekday['weekday']} Adelaide {_count_note(weekday['posts'])}"
    return f"{block['label']} Adelaide {_count_note(block['posts'])}"


def ratio(num, den):
    if not den:
        return 0.0
    return float(num) / float(den)


def funnel_stats(posts):
    if posts is None or posts.empty:
        return {
            "posts": 0,
            "impressions": 0,
            "detail_expands": 0,
            "profile_visits": 0,
            "new_follows": 0,
            "expand_rate": 0.0,
            "visit_rate": 0.0,
            "follow_rate": 0.0,
        }
    impressions = int(posts["impressions"].sum())
    expands = int(posts["detail_expands"].sum())
    visits = int(posts["profile_visits"].sum())
    follows = int(posts["new_follows"].sum())
    return {
        "posts": int(len(posts)),
        "impressions": impressions,
        "detail_expands": expands,
        "profile_visits": visits,
        "new_follows": follows,
        "expand_rate": ratio(expands, impressions),
        "visit_rate": ratio(visits, expands),
        "follow_rate": ratio(follows, visits),
    }


def funnel(posts, keywords=None):
    """Impressions → detail expands → profile visits → new follows."""
    keywords = keywords or DEFAULT_KEYWORDS
    if posts is None or posts.empty:
        return {"overall": funnel_stats(posts), "by_type": [], "top_follows": []}
    overall = funnel_stats(posts)
    by_type = []
    labels = set()
    classified = []
    for _, post in posts.iterrows():
        tags = classify_post(post.get("text", ""), keywords)
        classified.append(tags)
        labels.update(tags)
    for label in labels:
        mask = [label in tags for tags in classified]
        subset = posts[mask]
        if len(subset) < MIN_TYPE_POSTS:
            continue
        stats = funnel_stats(subset)
        stats["type"] = label
        by_type.append(stats)
    by_type.sort(key=lambda row: (-row["follow_rate"], -row["new_follows"], row["type"]))
    top, _bottom = ranked_posts(posts, "new_follows")
    return {"overall": overall, "by_type": by_type, "top_follows": top}


def is_original(text):
    return "original" in classify_post(text, [])


def posting_streak(posts):
    """Consecutive Adelaide days with at least one original post, ending at the latest."""
    if posts is None or posts.empty:
        return {"days": 0, "until": None}
    originals = posts[posts["text"].map(is_original)]
    if originals.empty:
        return {"days": 0, "until": None}
    days = set(originals["adelaide_date"].tolist())
    latest = max(days)
    cursor = latest
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return {"days": streak, "until": latest.isoformat()}


def pick_examples(posts):
    """Do-more: best engagement rate among posts above median impressions."""
    if posts is None or posts.empty:
        return {"do_more": None, "underperformer": None, "median_impressions": None}
    median_imp = float(posts["impressions"].median())
    notable = posts[posts["impressions"] > median_imp]
    if notable.empty:
        notable = posts
    best = notable.sort_values("engagement_rate", ascending=False).iloc[0]
    worst = notable.sort_values("engagement_rate", ascending=True).iloc[0]
    do_more = post_ref(best)
    under = post_ref(worst)
    if do_more["post_id"] == under["post_id"]:
        under = None
    return {
        "do_more": do_more,
        "underperformer": under,
        "median_impressions": median_imp,
    }


def consistency(overview, content):
    end = latest_export_date(overview, content)
    this_span, prev_span = week_windows(end)
    this_posts = in_days(content, *this_span)
    prev_posts = in_days(content, *prev_span)
    window_days = (this_span[1] - this_span[0]).days + 1
    posts_this = int(len(this_posts)) if this_posts is not None else 0
    posts_prev = int(len(prev_posts)) if prev_posts is not None else 0
    examples = pick_examples(this_posts)
    return {
        "streak": posting_streak(content),
        "window_days": window_days,
        "posts_per_day": posts_this / window_days if window_days else 0.0,
        "posts": change(posts_this, posts_prev),
        "do_more": examples["do_more"],
        "underperformer": examples["underperformer"],
        "median_impressions": examples["median_impressions"],
    }


def week_bounds(start, end):
    """Inclusive UTC dates → [since, until) for Data._in_window."""
    since = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    until = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    return since, until


def infer_me_id(data_dir):
    """Owner id from posts_*.json (get_users_posts for the connected account)."""
    for _path, doc in xn.load(os.path.join(data_dir, "posts_*.json")):
        for post in doc.get("data") or []:
            if isinstance(post, dict) and post.get("author_id"):
                return str(post["author_id"])
    return None


def load_me_id(config_path=None, data_dir=None):
    if config_path:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and cfg.get("user_id"):
            return str(cfg["user_id"])
    if data_dir:
        return infer_me_id(data_dir)
    return None


def load_neighbour_data(data_dir, me_id=None):
    """Reuse x_neighbours.Data. Omit data_dir to skip follower share."""
    if not data_dir:
        return None
    if not os.path.isdir(data_dir):
        raise SystemExit(f"data folder not found: {data_dir}")
    me_id = infer_me_id(data_dir) or me_id
    if not me_id:
        return None
    return xn.Data(data_dir, str(me_id))


def timestamp_in_followers_doc(doc):
    """Prefer a saved fetch time on the file or its meta, if one exists."""
    if not isinstance(doc, dict):
        return None
    sources = [doc]
    meta = doc.get("meta")
    if isinstance(meta, dict):
        sources.append(meta)
    for source in sources:
        for key in ("fetched_at", "as_of", "saved_at"):
            value = source.get(key)
            if not value:
                continue
            try:
                return xn.ts(value)
            except (TypeError, ValueError):
                continue
    return None


def follower_snapshot_time(data_dir):
    """Newest followers_*.json: timestamp inside the file, else mtime."""
    paths = glob.glob(os.path.join(data_dir, "followers_*.json"))
    if not paths:
        return None
    newest = max(paths, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        doc = None
    inside = timestamp_in_followers_doc(doc) if doc else None
    if inside is not None:
        return inside
    return datetime.fromtimestamp(os.path.getmtime(newest), tz=timezone.utc)


def _side_share(counts, followers):
    people_f = people_n = 0
    actions_f = actions_n = 0
    for uid, n in counts.items():
        if uid in followers:
            people_f += 1
            actions_f += n
        else:
            people_n += 1
            actions_n += n
    people = people_f + people_n
    actions = actions_f + actions_n
    return {
        "people": {
            "followers": people_f,
            "non_followers": people_n,
            "total": people,
            "follower_share": ratio(people_f, people),
        },
        "actions": {
            "followers": actions_f,
            "non_followers": actions_n,
            "total": actions,
            "follower_share": ratio(actions_f, actions),
        },
    }


def follower_share(data, start, end):
    """Follower vs non-follower share of this week's likers, reposters, and repliers.

    The follower set is the current `followers_*.json` snapshot that Data already
    keeps. Likes and reposts have no action time, so
    they count on owner posts in the week, same as build_activity. Replies use
    the mention timestamp and the same “reply to me” rule as Data.window.
    """
    since, until = week_bounds(start, end)
    likes, reposts, replies = Counter(), Counter(), Counter()
    me = data.me
    for pid, post in data.posts.items():
        if not data._in_window(post.get("created_at"), since, until):
            continue
        for uid in data.likes.get(pid, []):
            if uid and uid != me:
                likes[uid] += 1
        for uid in data.reposts.get(pid, []):
            if uid and uid != me:
                reposts[uid] += 1
    for mention in data.mentions:
        uid = mention.get("author_id")
        if not uid or uid == me:
            continue
        if not data._in_window(mention.get("created_at"), since, until):
            continue
        refs = [ref.get("id") for ref in xn.referenced(mention)
                if ref.get("type") == "replied_to"]
        if mention.get("in_reply_to_user_id") == me or any(ref in data.posts for ref in refs):
            replies[uid] += 1
    followers = data.followers
    overall = likes + reposts + replies
    repeat = sum(1 for uid, n in overall.items() if uid not in followers and n >= 2)
    when = follower_snapshot_time(data.data_dir)
    snap_date = when.astimezone(timezone.utc).date() if when else None
    age = (end - snap_date).days if snap_date else None
    return {
        "overall": _side_share(overall, followers),
        "likes": _side_share(likes, followers),
        "reposts": _side_share(reposts, followers),
        "replies": _side_share(replies, followers),
        "repeat_non_followers": repeat,
        "snapshot_date": snap_date.isoformat() if snap_date else None,
        "follower_snapshot_age_days": age,
    }


def analyze(folder, keywords=None, data_dir=None, me_id=None):
    keywords = keywords or DEFAULT_KEYWORDS
    data = load_exports(folder)
    overview, content = data["overview"], data["content"]
    card = scorecard(overview, content)
    start = datetime.fromisoformat(card["this_week"][0]).date()
    end = datetime.fromisoformat(card["this_week"][1]).date()
    this_posts = in_days(content, start, end)
    report = {
        "keywords": list(keywords),
        "scorecard": card,
        "types": content_types(this_posts, keywords),
        "times": posting_times(this_posts, keywords),
        "funnel": funnel(this_posts, keywords),
        "consistency": consistency(overview, content),
    }
    neighbours = load_neighbour_data(data_dir, me_id)
    if neighbours is not None:
        report["follower_share"] = follower_share(neighbours, start, end)
    return report


def json_ready(value):
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return json_ready(value.item())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def fmt_change(item):
    if not item or item.get("previous") == 0:
        return "from nothing last week" if item and item.get("this") else "same as last week"
    pct = item.get("pct")
    if not pct:
        return "same as last week"
    if pct > 0:
        return f"up {pct:.0%}"
    return f"down {abs(pct):.0%}"


def fmt_rate(value):
    pct = (value or 0) * 100
    if pct >= 10:
        return f"{pct:.0f}%"
    if pct >= 1:
        return f"{pct:.1f}%"
    return f"{pct:.2f}%"


def short_span(start, end):
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end)
    if a.month == b.month and a.year == b.year:
        return f"{a.day}–{b.day} {a.strftime('%b')}"
    return f"{a.day} {a.strftime('%b')}–{b.day} {b.strftime('%b')}"


def quoted(post):
    if not post:
        return ""
    return '"' + post["text"] + '"'


def render_markdown(report):
    """Short paste-in section for the Sunday report. Aim for about 15 lines."""
    card = report["scorecard"]
    types = report["types"]
    times = report["times"]
    funnel_data = report["funnel"]
    cons = report["consistency"]
    totals = card["totals"]
    lines = ["## What worked this week", ""]
    lines.append(
        f"{short_span(*card['this_week'])} vs {short_span(*card['previous_week'])}. "
        f"Impressions {xn.fmt_num(totals['impressions']['this'])} ({fmt_change(totals['impressions'])}). "
        f"New follows {xn.fmt_num(totals['new_follows']['this'])} ({fmt_change(totals['new_follows'])}). "
        f"{cons['posts']['this']} posts, {cons['posts_per_day']:.1f} a day "
        f"(was {cons['posts']['previous']} last week)."
    )
    lines.append("")
    top_imp = (card["posts"]["impressions"]["top"] or [None])[0]
    top_fol = (card["posts"]["new_follows"]["top"] or [None])[0]
    quiet = (card["posts"]["impressions"]["bottom"] or [None])[0]
    if top_imp:
        lines.append(
            f"Best reach: {quoted(top_imp)} — {xn.fmt_num(top_imp['impressions'])} impressions.")
    if top_fol:
        lines.append(
            f"Best follows: {quoted(top_fol)} — {top_fol['new_follows']} new follows.")
    if quiet:
        lines.append(
            f"Quietest: {quoted(quiet)} — {xn.fmt_num(quiet['impressions'])} impressions.")
    lines.append("")
    def pretty_type(name):
        return name if name[:1].isupper() else name.capitalize()

    lead = [pretty_type(row["type"]) for row in types[:2]]
    if len(lead) >= 2:
        type_bit = f"{lead[0]} and {lead[1]} posts had the strongest median engagement."
    elif lead:
        type_bit = f"{lead[0]} posts had the strongest median engagement."
    else:
        type_bit = "No content type had three posts this week."
    lines.append(
        type_bit
        + " Types with fewer than 3 posts are left out. Media is skipped because the CSV does not mark it."
    )
    phrase = best_time_phrase(times)
    if phrase:
        drop_bit = ", after dropping the loudest 5% of posts" if times.get("dropped") else ""
        lines.append(f"Best time to post: {phrase}{drop_bit}.")
    type_bits = []
    for row in times.get("by_type") or []:
        bit = best_time_phrase(row)
        if not bit:
            continue
        type_bits.append(f"{pretty_type(row['type'])} {bit}")
        if len(type_bits) == 2:
            break
    if type_bits:
        lines.append("By type: " + "; ".join(type_bits) + ".")
    overall = funnel_data["overall"]
    lines.append(
        f"Funnel: {fmt_rate(overall['expand_rate'])} of impressions opened the post, "
        f"{fmt_rate(overall['visit_rate'])} of those visited the profile, "
        f"{fmt_rate(overall['follow_rate'])} of visits followed."
    )
    streak = cons["streak"]
    lines.append(
        f"Streak: {streak['days']} Adelaide days in a row with an original post."
    )
    share = report.get("follower_share")
    if share and share["overall"]["people"]["total"]:
        people = share["overall"]["people"]
        actions = share["overall"]["actions"]
        likes = share["likes"]["actions"]
        reposts = share["reposts"]["actions"]
        replies = share["replies"]["actions"]
        label = "Followers"
        if share.get("snapshot_date"):
            day = date.fromisoformat(share["snapshot_date"])
            label = f"Followers (list from {day.day} {day.strftime('%b')})"
        line = (
            f"{label}: {fmt_rate(people['follower_share'])} of people who engaged "
            f"({fmt_rate(ratio(people['non_followers'], people['total']))} not), "
            f"{fmt_rate(actions['follower_share'])} of actions. "
            f"Likes {fmt_rate(likes['follower_share'])}, "
            f"reposts {fmt_rate(reposts['follower_share'])}, "
            f"replies {fmt_rate(replies['follower_share'])}."
        )
        age = share.get("follower_snapshot_age_days")
        if age is not None and age > SNAPSHOT_STALE_DAYS:
            line += (
                f" (follower list is {age} days old, so refresh it for accurate numbers)"
            )
        lines.append(line)
        repeat = share["repeat_non_followers"]
        if repeat == 1:
            lines.append("1 non-follower engaged 2+ times (likely follow-back).")
        else:
            lines.append(f"{repeat} non-followers engaged 2+ times.")
    lines.append("")
    if cons.get("do_more"):
        lines.append(f"Do more of this: {quoted(cons['do_more'])}.")
    if cons.get("underperformer"):
        lines.append(f"Ease off: {quoted(cons['underperformer'])}.")
    return "\n".join(lines).rstrip() + "\n"


def write_report(report, out_dir, write_json=False):
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "what_worked_week.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    paths = {"markdown": md_path, "json": None}
    if write_json:
        json_path = os.path.join(out_dir, "what_worked_week.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(json_ready(report), fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        paths["json"] = json_path
    return paths


def export_mtime(folder):
    """UTC time of the newest content_*.csv, used as Analytics export time."""
    paths = csv_paths(folder, "content")
    if not paths:
        raise SystemExit(f"no content_*.csv in {folder}")
    return datetime.fromtimestamp(os.path.getmtime(paths[0]), tz=timezone.utc)


def status_id_from_url(url):
    """Status id from a post URL, ignoring query strings and trailing slashes."""
    text = str(url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    match = re.search(r"/status/(\d+)", text)
    return match.group(1) if match else ""


def _int_count(row, key):
    try:
        value = row[key]
    except (KeyError, IndexError):
        return 0
    if pd.isna(value):
        return 0
    return int(value)


def post_follows_rows(posts, exported_at):
    """One row per post. New follows is X's count as of export, not strictly 48h."""
    include_quotes = posts is not None and not posts.empty and "quotes" in posts.columns
    rows = []
    if posts is None or posts.empty:
        return rows, include_quotes
    for _, post in posts.iterrows():
        age = (exported_at - post["posted_at"]).total_seconds() / 3600.0
        pid = str(post["post_id"])
        row = {
            "post_id": pid,
            "post_url": f"https://x.com/ronyspark/status/{pid}",
            "posted_at_adelaide": post["adelaide_at"].isoformat(timespec="seconds"),
            "age_hours_at_export": round(age, 2),
            "impressions": _int_count(post, "impressions"),
            "profile_visits": _int_count(post, "profile_visits"),
            "new_follows": _int_count(post, "new_follows"),
            "replies": _int_count(post, "replies"),
            "likes": _int_count(post, "likes"),
            "reposts": _int_count(post, "reposts"),
            "follows_window_note": "within_48h" if age <= 48 else "lifetime_to_export",
        }
        if include_quotes:
            row["quotes"] = _int_count(post, "quotes")
        rows.append(row)
    return rows, include_quotes


def post_follows_fieldnames(include_quotes):
    cols = [
        "post_id", "post_url", "posted_at_adelaide", "age_hours_at_export",
        "impressions", "profile_visits", "new_follows", "replies",
    ]
    if include_quotes:
        cols.append("quotes")
    cols.extend(["likes", "reposts", "follows_window_note"])
    return cols


def write_csv_atomic(path, fieldnames, rows):
    dest = os.path.abspath(path)
    folder = os.path.dirname(dest) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".csv", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_post_follows_csv(path, posts, exported_at):
    """Write the per-post follows CSV for the content bot's 48-hour review."""
    rows, include_quotes = post_follows_rows(posts, exported_at)
    write_csv_atomic(path, post_follows_fieldnames(include_quotes), rows)
    return path


def fill_review_log(path, posts, exported_at):
    """Fill or overwrite follows_48h on an existing CSV, matching post_url on status id.

    Only posts aged <= 72h at export get a number; older or unmatched rows are blank.
    Other columns and row order are preserved. Writes via a temp file then rename.
    """
    follows = {}
    if posts is not None and not posts.empty:
        for _, post in posts.iterrows():
            age = (exported_at - post["posted_at"]).total_seconds() / 3600.0
            pid = str(post["post_id"])
            follows[pid] = str(_int_count(post, "new_follows")) if age <= 72 else ""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "follows_48h" not in fieldnames:
        fieldnames.append("follows_48h")
    for row in rows:
        row["follows_48h"] = follows.get(status_id_from_url(row.get("post_url", "")), "")
    write_csv_atomic(path, fieldnames, rows)
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Weekly content analytics from X Analytics CSV exports. No X API calls.")
    parser.add_argument("--exports", required=True, help="folder with content_*.csv and overview_*.csv")
    parser.add_argument("--out", default=".", help="folder for what_worked_week.md")
    parser.add_argument("--json", action="store_true", help="also write what_worked_week.json")
    parser.add_argument("--config", help="optional config.json for keyword_tags")
    parser.add_argument("--data", help="optional folder of saved X JSON; skip follower share if omitted")
    parser.add_argument(
        "--post-follows-csv",
        help="write one row per post for the 48-hour review",
    )
    parser.add_argument(
        "--review-log",
        help="fill follows_48h on an existing CSV by post_url status id",
    )
    args = parser.parse_args()
    report = analyze(
        args.exports, load_keywords(args.config),
        data_dir=args.data, me_id=load_me_id(args.config, args.data),
    )
    paths = write_report(report, args.out, args.json)
    if args.post_follows_csv or args.review_log:
        posts = load_content(args.exports)
        when = export_mtime(args.exports)
        if args.post_follows_csv:
            write_post_follows_csv(args.post_follows_csv, posts, when)
            paths["post_follows_csv"] = args.post_follows_csv
        if args.review_log:
            fill_review_log(args.review_log, posts, when)
            paths["review_log"] = args.review_log
    print(json.dumps(paths, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
