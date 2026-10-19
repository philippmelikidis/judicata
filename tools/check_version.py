#!/usr/bin/env python3
"""Fail when a change that ships to users has no new version and no changelog entry.

Claude Code updates a plugin with a version in plugin.json only when that version
goes up. So every change to a shipped file since the latest release tag (v*) needs
either a higher version in .claude-plugin/plugin.json (the next release) or an entry
under "## [Unreleased]" in CHANGELOG.md (released later). Shipped files are the
manifests, skills, agents, hooks, scripts and templates. Uncommitted changes count.

  python3 tools/check_version.py    exit 0 when fine, else exit 1 with the reason

Without any release tag yet there is nothing to compare, and the check passes.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

SHIPPED = (".claude-plugin/", "skills/", "agents/", "hooks/", "scripts/", "templates/")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _version(text: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    return tuple(int(part) for part in match.groups())


def _latest_tag() -> str | None:
    tags = [
        t
        for t in _git("tag", "--list", "v*").split()
        if re.fullmatch(r"v\d+\.\d+\.\d+", t)
    ]
    return max(tags, key=_version) if tags else None


def _unreleased(changelog: Path) -> bool:
    """True when the Unreleased section of the changelog has any entry."""
    if not changelog.is_file():
        return False
    text = changelog.read_text(encoding="utf-8")
    match = re.search(
        r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    return bool(match and match.group(1).strip())


def main() -> int:
    tag = _latest_tag()
    if tag is None:
        print("no release tag yet, nothing to compare")
        return 0
    manifest = json.loads(
        Path(".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    version = manifest.get("version", "")
    if _version(version) < _version(tag):
        print(f"plugin.json version {version} is lower than the latest release {tag}")
        return 1
    changed = sorted(
        {
            *_git("diff", "--name-only", tag, "--").split(),
            *_git("ls-files", "--others", "--exclude-standard").split(),
        }
    )
    shipped = [path for path in changed if path.startswith(SHIPPED)]
    if not shipped:
        print(f"no shipped file changed since {tag}")
        return 0
    if _version(version) > _version(tag):
        print(f"shipped files changed since {tag}, plugin.json is at {version}: fine")
        return 0
    if _unreleased(Path("CHANGELOG.md")):
        print(
            f"shipped files changed since {tag}, listed under Unreleased in CHANGELOG.md"
        )
        return 0
    print(
        f"shipped files changed since {tag}, but plugin.json is still at {version} and"
        " CHANGELOG.md has no Unreleased entry. Add an entry under '## [Unreleased]',"
        " or raise the version for a release. Changed:"
    )
    for path in shipped:
        print(f"  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
