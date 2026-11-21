"""X read params. The checks live in tests/test_fetch_plan.py."""


class LikerFetchRefused(Exception):
    pass


def liking_users_params(run, post_id):
    if run != "week":
        raise LikerFetchRefused(run)
    return {
        "id": str(post_id),
        "max_results": 100,
        "user.fields": "username,name,description,public_metrics,profile_image_url",
    }


def owner_timeline_params(user_id, start_time):
    return {
        "id": str(user_id),
        "start_time": start_time,
        "exclude": "retweets",
        "max_results": 100,
        "post.fields": "created_at,public_metrics,in_reply_to_user_id,referenced_tweets,conversation_id",
        "expansions": "author_id,referenced_posts",
        "user.fields": "username,name,profile_image_url,public_metrics",
    }


_REPOST_USER_FIELDS = (
    "username",
    "name",
    "description",
    "public_metrics",
    "profile_image_url",
)


def reposted_by_params(post_id):
    return {
        "id": str(post_id),
        "max_results": 100,
        "user.fields": ",".join(_REPOST_USER_FIELDS),
    }


def page_likers(fetch_page):
    """fetch_page(token) -> one response. token is None on the first call."""
    seen, token, found = set(), None, []
    while True:
        page = fetch_page(token)
        added = 0
        for user in page.get("data") or []:
            uid = user.get("id")
            if uid is None or str(uid) in seen:
                continue
            seen.add(str(uid))
            found.append(user)
            added += 1
        token = (page.get("meta") or {}).get("next_token") or None
        if not token or added == 0:
            break
    return found
