#!/usr/bin/env python3
"""Fail when a change that ships to users has no new version and no changelog entry.

Claude Code updates a plugin with a version in plugin.json only when that version
goes up. So every change to a shipped file since the latest release tag (v*, the
newest one reachable from HEAD) needs either a higher version in
.claude-plugin/plugin.json (the next release) or a new entry under "## [Unreleased]"
in CHANGELOG.md (released later). Shipped files are the manifests, skills, agents,
hooks, scripts and templates; a file moved out of them counts as well. Uncommitted
changes count. An Unreleased entry counts only if it was not already there at the tag.
The version may never go below the latest tag.

  python3 tools/check_version.py    exit 0 when fine, else exit 1 with the reason

Without any release tag yet there is nothing to compare, and the check passes.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

SHIPPED = (".claude-plugin/", "skills/", "agents/", "hooks/", "scripts/", "templates/")
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _version(text: str) -> tuple[int, int, int, int]:
    """Comparable form of a version; a prerelease sorts before its release."""
    match = VERSION.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), 0 if pre else 1


def _latest_tag() -> str | None:
    tags = [
        tag
        for tag in _git("tag", "--list", "v*", "--merged", "HEAD").split()
        if VERSION.fullmatch(tag)
    ]
    return max(tags, key=_version) if tags else None


def _changed(tag: str) -> list[str]:
    """Paths changed since the tag, both sides of a rename, plus untracked files."""
    diff = _git("diff", "--name-only", "--no-renames", "-z", tag, "--")
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z")
    return sorted({path for path in (diff + untracked).split("\0") if path})


def _entries(text: str) -> set[str]:
    """Entries ("- ..." or "* ...") under "## [Unreleased]" of a changelog."""
    match = re.search(
        r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        return set()
    body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL)
    return {
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith(("- ", "* "))
    }


def _new_unreleased(tag: str) -> bool:
    changelog = Path("CHANGELOG.md")
    if not changelog.is_file():
        return False
    now = _entries(changelog.read_text(encoding="utf-8"))
    try:
        then = _entries(_git("show", f"{tag}:CHANGELOG.md"))
    except subprocess.CalledProcessError:
        then = set()
    return bool(now - then)


def main() -> int:
    tag = _latest_tag()
    if tag is None:
        print("no release tag yet, nothing to compare")
        return 0
    manifest = json.loads(
        Path(".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    version = str(manifest.get("version", ""))
    if _version(version) < _version(tag):
        print(f"plugin.json version {version} is lower than the latest release {tag}")
        return 1
    shipped = [path for path in _changed(tag) if path.startswith(SHIPPED)]
    if not shipped:
        print(f"no shipped file changed since {tag}")
        return 0
    if _version(version) > _version(tag):
        print(f"shipped files changed since {tag}, plugin.json is at {version}: fine")
        return 0
    if _new_unreleased(tag):
        print(
            f"shipped files changed since {tag}, listed under Unreleased in CHANGELOG.md"
        )
        return 0
    print(
        f"shipped files changed since {tag}, but plugin.json is still at {version} and"
        " CHANGELOG.md has no new entry under Unreleased. Add one under"
        " '## [Unreleased]', or raise the version for a release. Changed:"
    )
    for path in shipped:
        print(f"  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
