#!/usr/bin/env python3
"""Read the X Neighbours version.

The number is the single line in VERSION, next to this file. Code and docs
read that file. Do not copy the number into another module.
"""
import os
import re
import sys

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def version(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    body = text[:-1] if text.endswith("\n") else text
    if not body or "\n" in body or body != body.strip() or not SEMVER.fullmatch(body):
        raise ValueError("VERSION must be a single major.minor.patch line")
    return body


def parse_semver(text):
    """major.minor.patch as a tuple. A leading v, or any other text, is refused."""
    body = (text or "").strip()
    if not SEMVER.fullmatch(body):
        raise ValueError("version must be major.minor.patch")
    return tuple(int(part) for part in body.split("."))


def is_newer(remote, local):
    """True when the remote version is a higher major.minor.patch than local."""
    return parse_semver(remote) > parse_semver(local)


def main():
    try:
        print(version())
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
