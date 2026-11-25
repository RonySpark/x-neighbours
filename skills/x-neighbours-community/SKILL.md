---
name: x-neighbours-community
description: >-
  Opt-in community mode for X Neighbours. Use when the owner joins or leaves
  the community, on the Sunday share reminder, on the Monday version check,
  or when sending the neighbours picture in community mode.
---

# X Neighbours — community mode

Community mode is off until the owner opts in. Individual mode is a private bot: local reports, the picture, and the good neighbours list. It does not check for updates, does not download the global list, does not share, and does not mention a tag.

Read [`skills/x-neighbours/SKILL.md`](../x-neighbours/SKILL.md) for the report itself. This file is the community gate. The store file is `community.json` next to `good_neighbours.json`. Never commit it.

```text
/cursor/stores/<store-id>/x-neighbours/community.json
/cursor/stores/<store-id>/x-neighbours/global_neighbours.neighbours
```

## Join and leave

On the first run, after the two report messages, run:

```bash
python3 community.py --file <community path> offer
```

If `offer` is true, send one yes/no card with `prompt`. Do not add a second explanation. If they say yes:

```bash
python3 community.py --file <community path> join --now <T>
```

Then create the Sunday routine below. If they say no, stop. `offer` records that you asked. Do not ask again on later runs. They can still say “join the community” later, and you run `join`.

To leave:

```bash
python3 community.py --file <community path> leave --now <T>
```

Leaving stops the update check, the Sunday reminder, global-list downloads, sharing, and the picture-tag reminder. Local reports keep working. A list already merged on GitHub stays until they file an opt-out. Tell them that in one sentence.

Before any community behaviour, run `status`. If `community` is false, do not fetch `versions/`, do not fetch the global file, and do not open a share pull request.

```bash
python3 community.py --file <community path> status
python3 community.py --file <community path> plan
```

`plan` says what a Monday check may touch. In individual mode `fetch_versions` and `fetch_global` are false. Obey that. Do not open those URLs anyway.

## Picture tag

When you send section 4 and community mode is on:

```bash
python3 community.py --file <community path> picture-tag --handle <config handle> --text "<thank_you.text>"
```

If `remind` is false, send the thank-you exactly as the main runbook says. No @handle in that message.

If `remind` is true, keep `thank_you.text` as the first line. Use `intent` instead of `thank_you.intent` for the “Post your thank-you” link, so the composer includes `@SkyXFunNext`. After `thank_you.note`, add `text`. That reminder is the only @handle in the message. The owner still attaches the picture and posts it. You do not post or repost.

The reminder is skipped when the posting account is already `@SkyXFunNext`.

## Sunday reminder

Create this when they join. Sundays at 9:00 AM in the owner’s timezone (the same `timezone` as reports, or UTC). It does not fetch X.

```bash
python3 community.py --file <community path> sunday --now <T> --timezone <IANA name>
```

If `remind` is false, send nothing. If it is true, send `text` and nothing else. When they can share, the line invites them. When they cannot, it names the day they can share again. It also says a feature release may land on Monday.

## Monday check

On the Monday report, after the two messages, and only when `plan` says to fetch:

1. Download `versions_url` from `plan` to a temp file. Do not commit it.
2. Run:

```bash
python3 community.py --file <community path> check-update --latest <temp file>
```

3. If `ask` is true, send `message` as one yes/no card. Do not `git pull` yet.
4. If they say no:

```bash
python3 community.py --file <community path> decide-upgrade --answer no --version <remote_version>
```

Stay on the current version. Do not ask again until the next Monday, unless they ask you to check.

5. If they say yes:

```bash
python3 community.py --file <community path> decide-upgrade --answer yes --version <remote_version>
```

`executed` is false. You run the `steps` yourself: `git pull --ff-only`, install requirements when that file changed, `python3 version.py`, read `CHANGELOG.md`, and tell them the new version in one line. Keep `config.json`, `data/`, the good neighbours file, `state.json`, `community.json`, and `global_neighbours.neighbours`.

6. On the same check, download `global_url` from `plan` and save it:

```bash
python3 community.py --file <community path> save-global --src <downloaded file> --weekly
```

Do this even when there is no new bot version. Skip it on every other day. Recommendations you show later come from this local file. The owner chooses who to follow. You do not follow anyone.

If they ask “check for updates” on another day, you may run the check with `--force` on `check-update`. Still ask before pulling. Do not download the global file mid-week.

Individual owners who ask to update follow the Upgrading section of the main runbook. They do not get this check on their own.
