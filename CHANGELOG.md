# Changelog

What changed in X Neighbours, newest first. The version number is the single line in [`VERSION`](VERSION). `version.py` reads that file. Docs point at it and do not keep a second copy.

```bash
python3 version.py
```

## Unreleased

- A `latest_*` file saved on an earlier UTC day than the other saved files is noted. A file from that same day is not, and a directory with no newer file is not.
- A saved reposter list or quote list uses the same short-list bar as likers: the file is present, the count is at least 10, and the saved rows are under 80% of that count.
- The report notes a missing quote file for the same shared budget as likers and reposters: last 24 hours when `quote_count` is above 0, and the rest of the 7 days when it is at least 3. The duplicate shared-budget lines for reposts and quotes are removed from the runbook. Weekly-only still fetches every count above 0.
- The report notes liker and reposter files the shared budget should already have: last 24 hours when the count is above 0, and the rest of the 7 days when it is at least 3. A liker file for a post with at least 10 likes is noted when under 80% of them were saved. A plain repost saved in `posts_*.json` does not add likes or reposts to the score.
- A depth-2 or depth-3 reply scores `thread_reply_factor` (default 0.5) times `reply_weight`, at most 3 per neighbour per root post. Depth 4 and deeper add none. `author_engaged_bonus` (default 1.0) times `reply_weight` applies once per neighbour per conversation when the owner replied there and the neighbour replied after. Those replies do not add `mutual_bonus`. The Replies section, the waiting list, and open items still list only first-level replies and quotes.
- `new_follower_credit` goes only to someone absent from the follower snapshot on or before the week start and present in the newest snapshot. No snapshot that old means no credit and one note.
- `--backfill-counts` adds missing `counts.csv` rows from follower snapshots, one Adelaide date each, and leaves an existing date as it is. A day with no following snapshot leaves following and mutuals blank. The trend skips a blank day for following, mutuals, and ratio. A second run writes the same file.
- The neighbours report lists who unfollowed between follower snapshots, grouped by the later file's date in the config timezone. A blank timezone uses Australia/Adelaide.
- `mutual_bonus` is added once for each reply from a mutual on an original post. A reply under the owner's own reply adds none.
- Replies lists only a first-level reply or a quote on the owner's original post, newest first. A reply under the owner's own reply drops off. Waiting is still one row per person, and only when their latest message in that thread has no reply from the owner. ([#39](https://github.com/Skybatan/x-neighbours/pull/39))
- The house-framed thank-you picture shows each neighbour's display name, with @username on the line below. A missing name stays @username alone. ([#38](https://github.com/Skybatan/x-neighbours/issues/38))
- What's new shows each good neighbour's newest original post. A quote of their own counts. A reply or a plain repost does not. The age is that post's `created_at` against the current UTC time, not the report window. ([#36](https://github.com/Skybatan/x-neighbours/issues/36))
- This fork keeps Echo snapshots (`--snapshot-dir`), no follower pull from the X API, the scoring knobs, `open_items.csv`, the counts trend, content analytics, and the verified-follower welcome list. Those runbook rules now live in [`skills/x-neighbours/SKILL.md`](skills/x-neighbours/SKILL.md). [`BOT.md`](BOT.md) only points there.

## 2026-11-16

- Contributor bots follow [`skills/x-neighbours-maintain/SKILL.md`](skills/x-neighbours-maintain/SKILL.md): open an issue, branch, one change per pull request, run the tests, update the docs and this changelog, and bump `VERSION` when releasing. An installed bot upgrades in place. The upgrade steps live in [`skills/x-neighbours/SKILL.md`](skills/x-neighbours/SKILL.md). ([#28](https://github.com/Skybatan/x-neighbours/issues/28))
- [`skills/x-neighbours/SKILL.md`](skills/x-neighbours/SKILL.md) is the only runbook. [`BOT.md`](BOT.md) only points there. ([#32](https://github.com/Skybatan/x-neighbours/pull/32))
- GitHub Actions runs the unit tests and an offline report on every pull request and on pushes to `main`. ([#30](https://github.com/Skybatan/x-neighbours/pull/30))
- The Contributors section sits at the top of the README, with a welcome. ([#25](https://github.com/Skybatan/x-neighbours/pull/25), [#35](https://github.com/Skybatan/x-neighbours/pull/35))
- The README thanks [RonySpark](https://github.com/RonySpark) for the first contribution. ([#23](https://github.com/Skybatan/x-neighbours/pull/23))
- A week report no longer crashes when What's new has more than five people and `created_at` is still an ISO string. Waiting is one row per person, and only when their latest message in that thread has no reply. The loader prefers `referenced_tweets` and falls back to `referenced_posts`. Posts, mentions, and likers pass `max_results` 100, and mentions keep paging until there is no `next_token`. ([#19](https://github.com/Skybatan/x-neighbours/pull/19))

## 2026-11-05

- Shorter report header. Suggestion options use the person's name. ([#18](https://github.com/Skybatan/x-neighbours/pull/18))
- The chat report reads on a phone: short sections, and the picture sits in the thank-you message. ([#16](https://github.com/Skybatan/x-neighbours/pull/16))
- Catch up hides a post the owner already liked, replied to, reposted, or quoted. ([#14](https://github.com/Skybatan/x-neighbours/pull/14))
- The owner can file feedback from chat. The bot drafts a link and does not post the issue. ([#12](https://github.com/Skybatan/x-neighbours/pull/12))
- Report sections renamed. A stale report offers a fresh one. ([#11](https://github.com/Skybatan/x-neighbours/pull/11))
- A good neighbours list, and a thank-you on every report. ([#10](https://github.com/Skybatan/x-neighbours/pull/10))

## 2026-11-04

- Catch up keeps original posts. A quote of their own counts. A reply or a plain repost does not. ([#9](https://github.com/Skybatan/x-neighbours/pull/9))
- The README shows the short fictional sample report. ([#8](https://github.com/Skybatan/x-neighbours/pull/8))
- Likes and Reposts drop the Total column. ([#7](https://github.com/Skybatan/x-neighbours/pull/7))
- The first run covers a short window and stays a short report. ([#6](https://github.com/Skybatan/x-neighbours/pull/6))
- Report redesign, with house frames on the neighbours picture. ([#5](https://github.com/Skybatan/x-neighbours/pull/5))
- Three rings, and both the daily and the Monday routine. ([#3](https://github.com/Skybatan/x-neighbours/pull/3))
- A simpler first run, and optional likes-only, replies-only, per-post, and per-day reports. ([#2](https://github.com/Skybatan/x-neighbours/pull/2))
- First published version of X Neighbours.
