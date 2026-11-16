#!/usr/bin/env python3
"""Content analytics from X Analytics CSV exports. No X API calls.

Reads content_*.csv, overview_*.csv, and optional video_*.csv from an exports
folder. Post time is not in the CSV; it is decoded from the Post id snowflake.
"""
import glob
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import x_neighbours as xn


TWITTER_EPOCH_MS = 1288834974657
ADELAIDE = ZoneInfo("Australia/Adelaide")

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
    "Profile visits": "profile_visits",
    "Detail Expands": "detail_expands",
    "URL Clicks": "url_clicks",
    "Hashtag Clicks": "hashtag_clicks",
    "Permalink Clicks": "permalink_clicks",
}
CONTENT_COUNTS = [
    "impressions", "likes", "engagements", "bookmarks", "shares", "new_follows",
    "replies", "reposts", "profile_visits", "detail_expands", "url_clicks",
    "hashtag_clicks", "permalink_clicks",
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
VIDEO_COLUMNS = {
    "Date": "date_raw",
    "Views": "views",
    "Watch Time (ms)": "watch_time_ms",
    "Completion Rate": "completion_rate",
    "Average Watch Time (ms)": "average_watch_time_ms",
    "Estimated Revenue": "estimated_revenue",
}


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
    return sorted(glob.glob(os.path.join(folder, f"{prefix}_*.csv")))


def to_counts(frame, columns):
    for col in columns:
        if col not in frame.columns:
            frame[col] = 0
            continue
        values = frame[col].astype(str).str.replace(",", "", regex=False)
        frame[col] = pd.to_numeric(values, errors="coerce").fillna(0)
    return frame


def read_tables(paths, rename, counts, skiprows=0):
    frames = []
    for path in paths:
        frame = pd.read_csv(path, encoding="utf-8-sig", skiprows=skiprows)
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
    posts["post_id"] = posts["post_id"].astype(str)
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


def load_video(folder):
    """Optional. The first line of a video export is a title row, then the header."""
    paths = csv_paths(folder, "video")
    if not paths:
        return pd.DataFrame(columns=list(VIDEO_COLUMNS.values()))
    frames = []
    for path in paths:
        skip = 0
        with open(path, encoding="utf-8-sig") as fh:
            first = fh.readline()
        if first and not first.lower().startswith("date"):
            skip = 1
        frames.append(read_tables([path], VIDEO_COLUMNS, [
            "views", "watch_time_ms", "completion_rate", "average_watch_time_ms",
            "estimated_revenue",
        ], skiprows=skip))
    video = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if video.empty:
        return video
    video = video.dropna(subset=["date"])
    video = video.drop_duplicates(subset=["date"], keep="first")
    return video.sort_values("date").reset_index(drop=True)


def load_exports(folder):
    """Load every content/overview/video CSV in folder. Video may be missing."""
    if not os.path.isdir(folder):
        raise SystemExit(f"exports folder not found: {folder}")
    content = load_content(folder)
    overview = load_overview(folder)
    video = load_video(folder)
    if content.empty and overview.empty:
        raise SystemExit(f"no content_*.csv or overview_*.csv in {folder}")
    return {"content": content, "overview": overview, "video": video}


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
