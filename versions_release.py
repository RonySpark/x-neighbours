#!/usr/bin/env python3
"""Write versions/latest.json on a Monday release.

The version number is read from VERSION. This script refuses to run on any
other UTC weekday, and it is the only writer of versions/latest.json.
global_build.py must not call it. A mid-week global rebuild leaves this
folder alone.

  python3 versions_release.py --repo . --now ISO --changelog "text" \\
      --source-commit SHA

It does not create the git tag or the GitHub Release. The Monday workflow
does that after this script succeeds.
"""
import argparse
import json
import os
import sys

import rules
import version as version_mod


def assert_monday(when):
    parsed = rules.parse_time(when)
    if parsed.weekday() != 0:
        raise ValueError("versions/latest.json may only change on Monday (UTC)")
    return parsed


def release_document(repo, when, changelog, source_commit, min_compatible=None):
    parsed = assert_monday(when)
    number = version_mod.version(os.path.join(repo, "VERSION"))
    minimum = min_compatible or number
    version_mod.parse_semver(minimum)
    if not str(changelog or "").strip():
        raise ValueError("a Monday release needs a changelog line")
    return {
        "version": number,
        "released_at": rules.iso(parsed),
        "min_compatible_version": minimum,
        "repo": rules.REPO_URL,
        "global_path": rules.GLOBAL_REL,
        "source_commit": str(source_commit or ""),
        "changelog": str(changelog).strip(),
    }


def write_release(repo, when, changelog, source_commit, min_compatible=None):
    document = release_document(repo, when, changelog, source_commit, min_compatible)
    folder = os.path.join(repo, "versions")
    os.makedirs(folder, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    for name in ("latest.json", document["version"] + ".json"):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return document


def main():
    parser = argparse.ArgumentParser(description="Publish versions/latest.json on Monday.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--now", required=True)
    parser.add_argument("--changelog", required=True)
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--min-compatible", default="")
    args = parser.parse_args()
    try:
        document = write_release(
            args.repo, args.now, args.changelog, args.source_commit,
            min_compatible=args.min_compatible or None)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "version": document["version"], "released_at": document["released_at"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
