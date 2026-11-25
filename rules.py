#!/usr/bin/env python3
"""Eligibility rules for a shared good neighbours list.

RULES.md is the explanation. This module is the copy the checker, the global
builder, and the tests run. Change a number here and in RULES.md together.
"""
import math
import re
from datetime import datetime, timedelta, timezone


RULES_VERSION = "1"
FORMAT_SUBMISSION = "x-neighbours-submission"
FORMAT_GLOBAL = "x-neighbours-global"
SUBMISSION_VERSION = 1
GLOBAL_VERSION = 1

LIKE_WEIGHT = 1
REPLY_WEIGHT = 3
CONVERSATION_FLOOR = 2
SCORE_NUMERATOR = 50
SCORE_WINDOW_DAYS = 90
SCORE_FLOOR = 8

DEFAULT_PERIOD_DAYS = 90
MIN_PERIOD_DAYS = 7
# The first 3-month picture covered 93 days. A share may use that window.
# Longer than that is refused. A longer window raises the minimum score.
MAX_PERIOD_DAYS = 93

LIST_CAP = 100
NOTE_MAX = 140
TAGS_MAX = 3
TAG_MAX_LEN = 24
SUBMISSION_INTERVAL_DAYS = 7
MIN_RECOMMENDATIONS = 2
MAX_FILE_BYTES = 256 * 1024

FOUNDING_OWNER_ID = "founding"
FOUNDING_OWNER_HANDLE = "founding-submission"
PICTURE_TAG = "SkyXFunNext"

REPO_SLUG = "Skybatan/x-neighbours"
REPO_URL = "https://github.com/Skybatan/x-neighbours"
GLOBAL_REL = "global/neighbours.global.neighbours"
BUILD_REL = "global/build.json"
OPTED_OUT_REL = "opted-out/accounts.json"
BANNED_REL = "banned/owners.json"
LATEST_REL = "versions/latest.json"
LOCAL_GLOBAL_NAME = "global_neighbours.neighbours"

LARGE_LIST = 80
OWNER_MIN_AGE_DAYS = 90
MAINTAINER_WAIVER_LABEL = "maintainer-waiver"

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
USER_ID_RE = re.compile(r"^[0-9]{1,25}$")
CODE_RE = re.compile(r"^XN-[A-Z0-9]{8}$")
PROOF_RE = re.compile(r"^[0-9]{1,25}$")
TAG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,23})$")
SUBMISSION_RE = re.compile(
    r"^submissions/([0-9]{1,25})/(\d{4}-\d{2}-\d{2}T\d{6}Z)\.neighbours$"
)
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z$")
LINK_RE = re.compile(r"(https?://|www\.|t\.co/|x\.com/)", re.IGNORECASE)
MENTION_RE = re.compile(r"@[A-Za-z0-9_]")
FOLLOW_BARGAIN_RE = re.compile(
    r"follow[\s-]*for[\s-]*follow|\bf4f\b|follow[\s-]*back|followback"
    r"|i(?:'ll| will) follow|follow me",
    re.IGNORECASE,
)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Unambiguous alphabet for a code the owner posts. The checker accepts any
# XN- code of eight letters or digits, so a hand-typed code still validates.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def as_dict():
    """The machine-readable rule card. rules.json mirrors this."""
    return {
        "rules_version": RULES_VERSION,
        "score": "likes + reposts + 3 * (replies + quotes)",
        "like_weight": LIKE_WEIGHT,
        "reply_weight": REPLY_WEIGHT,
        "min_score": "max(8, ceil(50 * period_days / 90))",
        "conversation_floor": CONVERSATION_FLOOR,
        "default_period_days": DEFAULT_PERIOD_DAYS,
        "min_period_days": MIN_PERIOD_DAYS,
        "max_period_days": MAX_PERIOD_DAYS,
        "cap": LIST_CAP,
        "submission_interval_days": SUBMISSION_INTERVAL_DAYS,
        "min_recommendations": MIN_RECOMMENDATIONS,
        "note_max_chars": NOTE_MAX,
        "max_tags": TAGS_MAX,
        "max_file_bytes": MAX_FILE_BYTES,
        "founding_owner_id": FOUNDING_OWNER_ID,
        "founding_owner_handle": FOUNDING_OWNER_HANDLE,
        "picture_tag": PICTURE_TAG,
    }


def parse_time(value):
    if isinstance(value, datetime):
        when = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        when = datetime.fromisoformat(text)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def iso(when):
    return parse_time(when).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_stamp(when):
    """UTC timestamp safe in a file name. Colons are removed."""
    return parse_time(when).strftime("%Y-%m-%dT%H%M%SZ")


def stamp_to_iso(stamp):
    match = STAMP_RE.fullmatch(stamp or "")
    if not match:
        raise ValueError("file stamp must look like 2026-11-25T170000Z")
    day, hour, minute, second = match.groups()
    return f"{day}T{hour}:{minute}:{second}Z"


def submission_relpath(user_id, when):
    return f"submissions/{user_id}/{file_stamp(when)}.neighbours"


def period_days(start, end):
    """Whole days covered by the period. An exact multiple stays exact."""
    seconds = (parse_time(end) - parse_time(start)).total_seconds()
    if seconds <= 0:
        raise ValueError("period ends before it starts")
    return int(math.ceil(seconds / 86400 - 1e-9))


def min_score(days):
    """max(8, ceil(50 * period_days / 90))."""
    days = int(days)
    if days < 1:
        raise ValueError("period_days must be at least 1")
    scaled = int(math.ceil(SCORE_NUMERATOR * days / SCORE_WINDOW_DAYS))
    return max(SCORE_FLOOR, scaled)


def interaction_score(likes, reposts, replies, quotes, like_weight=LIKE_WEIGHT,
                      reply_weight=REPLY_WEIGHT):
    return (int(likes) + int(reposts)) * like_weight + (int(replies) + int(quotes)) * reply_weight


def counts_of(interactions):
    if not isinstance(interactions, dict):
        raise ValueError("interactions must be an object")
    out = {}
    for key in ("replies", "quotes", "reposts", "likes"):
        value = interactions.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        out[key] = value
    return out


def eligibility(interactions, days):
    """Return (ok, reason). reason is empty when the row passes."""
    counts = counts_of(interactions)
    conversation = counts["replies"] + counts["quotes"]
    if conversation < CONVERSATION_FLOOR:
        return False, "conversation"
    score = interaction_score(
        counts["likes"], counts["reposts"], counts["replies"], counts["quotes"])
    needed = min_score(days)
    if score < needed:
        return False, "score"
    return True, ""


def score_of(interactions):
    counts = counts_of(interactions)
    return interaction_score(
        counts["likes"], counts["reposts"], counts["replies"], counts["quotes"])


def conversation_of(interactions):
    counts = counts_of(interactions)
    return counts["replies"] + counts["quotes"]


def note_problem(note):
    """Empty string when a note is allowed. None means the note is absent."""
    if note is None:
        return ""
    if not isinstance(note, str):
        return "note must be text"
    if note != note.strip() or not note:
        return "note must be non-empty text without surrounding spaces"
    if len(note) > NOTE_MAX:
        return f"note is longer than {NOTE_MAX} characters"
    if LINK_RE.search(note):
        return "note must not contain a link"
    if MENTION_RE.search(note):
        return "note must not contain an @mention"
    if FOLLOW_BARGAIN_RE.search(note):
        return "note must not promise a follow or ask for one"
    return ""


def tags_problem(tags):
    if tags is None:
        return ""
    if not isinstance(tags, list):
        return "tags must be a list"
    if len(tags) > TAGS_MAX:
        return f"at most {TAGS_MAX} tags"
    seen = set()
    for tag in tags:
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
            return "a tag must be a short lowercase word"
        if tag in seen:
            return "tags must not repeat"
        seen.add(tag)
    return ""


def follow_bargain(text):
    return bool(FOLLOW_BARGAIN_RE.search(text or ""))


def next_allowed_at(previous, interval_days=SUBMISSION_INTERVAL_DAYS):
    """UTC time when the next submission may be filed. None if there is no prior one."""
    if previous is None:
        return None
    return parse_time(previous) + timedelta(days=int(interval_days))


def submission_allowed(now, previous, interval_days=SUBMISSION_INTERVAL_DAYS):
    nxt = next_allowed_at(previous, interval_days)
    if nxt is None:
        return True, None
    current = parse_time(now)
    return current >= nxt, iso(nxt)


def rank_key(row):
    """Highest score, then replies plus quotes, then user id."""
    conversation = int(row["replies"]) + int(row["quotes"])
    return (-int(row["score"]), -conversation, str(row["user_id"]))


def select_neighbours(rows, days, owner_id, owner_handle="", drop_ids=None,
                      exclude_ids=None, cap=LIST_CAP):
    """Keep eligible people, highest score first, at most `cap`.

    rows are dicts with user_id, handle, replies, quotes, reposts, likes.
    The owner is always dropped. drop_ids are people the owner removed.
    exclude_ids are opted-out or otherwise barred user ids.
    """
    owner = str(owner_id)
    owner_name = str(owner_handle or "").lstrip("@").lower()
    dropped_by_owner = {str(item) for item in (drop_ids or [])}
    barred = {str(item) for item in (exclude_ids or [])}
    seen = set()
    kept = []
    dropped = []

    def reject(row, reason):
        dropped.append({
            "user_id": str(row.get("user_id") or ""),
            "handle": str(row.get("handle") or "").lstrip("@"),
            "reason": reason,
        })

    for row in rows:
        user_id = str(row.get("user_id") or "")
        handle = str(row.get("handle") or "").lstrip("@")
        if not user_id:
            reject(row, "missing id")
            continue
        if user_id == owner or (owner_name and handle.lower() == owner_name):
            reject(row, "self")
            continue
        if user_id in dropped_by_owner:
            reject(row, "removed")
            continue
        if user_id in barred:
            reject(row, "excluded")
            continue
        if user_id in seen:
            reject(row, "duplicate")
            continue
        seen.add(user_id)
        interactions = {
            "replies": int(row.get("replies") or 0),
            "quotes": int(row.get("quotes") or 0),
            "reposts": int(row.get("reposts") or 0),
            "likes": int(row.get("likes") or 0),
        }
        ok, reason = eligibility(interactions, days)
        if not ok:
            reject(row, reason)
            continue
        kept.append({
            **row,
            "user_id": user_id,
            "handle": handle,
            "replies": interactions["replies"],
            "quotes": interactions["quotes"],
            "reposts": interactions["reposts"],
            "likes": interactions["likes"],
            "score": interaction_score(
                interactions["likes"], interactions["reposts"],
                interactions["replies"], interactions["quotes"]),
        })
    kept.sort(key=rank_key)
    overflow = kept[cap:]
    for row in overflow:
        reject(row, "cap")
    return kept[:cap], dropped
