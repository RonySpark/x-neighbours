# Founding list

The global list starts empty. This folder does not contain a founding submission, and it should not. Do not commit a pre-made list of neighbours here.

The founding list is the first real submission through the normal share flow: one file at `submissions/<x user id>/<UTC timestamp>.neighbours`. That file keeps the submitter's real `user_id` and handle. The path has to match that user id, and validation checks it the same way as any other share. The neutral names `founding` and `founding-submission` are not written into the file.

## Point the waiver at that file

After that one submission is merged, set `founding_waiver.submission` in [`global/build.json`](../global/build.json) to that exact path. Leave the credit fields as the neutral identity:

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

`owner_id` and `owner_handle` are what the global list displays. They are not the submitter's handle. Only that exact path bypasses the two-recommendation rule. A later file from the same owner, or a file under `founding/`, does not.

The waiver path is blank in this repo until that first file exists. An empty path does nothing.

Open the config change as its own pull request and add the label `maintainer-waiver`. Without that label the validate Action rejects a waiver-path edit. Neighbour Dog must not merge it. The maintainer merges it.

The rebuild then loads that file. Each account in it enters `global/neighbours.global.neighbours` with `source` set to `founding` and one `recommended_by` row for `founding` / `founding-submission`. The row's `submission` is the real path. The submitter's handle is not copied into that credit. Until the path is set, a single owner's submission stays out of the global list.

Opt-out still removes a founding entry. A banned owner's later submissions do not change this seed. A founding entry stays until opt-out. If another owner later recommends the same account, that row is appended and `source` stays `founding`. The displayed handle of the neighbour refreshes from the newest recommendation.

## What this file is not

It is not a list of real neighbours, and it is not credited to the repo owner. The owner tests the path with a new bot. Neighbour Dog flags a first-time owner, so the maintainer reviews that first pull request before setting the waiver.
