# Founding list

The global list starts empty. This folder does not contain a founding submission yet.

The first real submission should come from a new bot, through the normal share flow in [`skills/x-neighbours-share/SKILL.md`](../skills/x-neighbours-share/SKILL.md). Do not put a personal name or handle on that list. The global builder credits the file named below as `founding` / `founding-submission`, even when a test bot wrote the file.

## Point the waiver at the file

After that one submission is merged, set `founding_waiver.submission` in [`global/build.json`](../global/build.json) to the exact repo path, for example:

```json
{
  "min_recommendations": 2,
  "submission_interval_days": 7,
  "rules_version": "1",
  "founding_waiver": {
    "submission": "submissions/123456789/2026-11-25T170000Z.neighbours",
    "owner_id": "founding",
    "owner_handle": "founding-submission"
  }
}
```

Leave `owner_id` and `owner_handle` as the neutral identity. Only that exact path bypasses the two-recommendation rule. A later file in this folder, or a submission that claims to be founding, does not.

Open that config change as its own pull request and add the label `maintainer-waiver`. Without that label the validate Action rejects a waiver-path edit. Neighbour Dog must not merge it. The maintainer merges it.

The rebuild Action then loads that file. Each account in it enters `global/neighbours.global.neighbours` with `source` set to `founding` and one `recommended_by` row for the neutral identity. Until the path is set, a single owner’s submission stays out of the global list, which is the normal two-recommendation rule.

Opt-out still removes a founding entry. A banned owner’s later submissions do not change this seed. A founding entry stays until opt-out. If another owner later recommends the same account, that row is appended and `source` stays `founding`. The displayed handle refreshes from the newest recommendation.

## What this file is not

It is not a list of real neighbours, and it is not credited to the repo owner. The owner tests the path with a new bot. Neighbour Dog flags a first-time owner, so the maintainer reviews that first pull request before setting the waiver.
