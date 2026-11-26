---
name: Share a neighbours list
about: Open a share when the bot could not open the pull request
title: "Share neighbours"
labels: []
---

Use this only when the bot prepared a `.neighbours` file and could not open the pull request (no `X_NEIGHBOURS_GITHUB_TOKEN`). Prefer the pull request from the share skill. One new file under `submissions/<user id>/<timestamp>.neighbours`. Do not edit older files. Do not change `global/build.json`.

### Path

`submissions/`

### Proof

The one-time code and the post id are inside the file. Neighbour Dog checks that the post is from the owner’s account.

### File

Attach the `.neighbours` file, or paste it when it is short. No follow-for-follow wording.

Recommendations are discovery only.
