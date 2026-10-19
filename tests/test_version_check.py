"""tools/check_version.py: a change that ships to users needs a new version or an
Unreleased entry in CHANGELOG.md. Run against throwaway Git repositories."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "check_version.py"

CHANGELOG = """# Changelog

## [Unreleased]
{unreleased}
## [0.1.0] - 2026-10-19

- First release.
"""


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def manifest(root, version):
    write(
        root,
        ".claude-plugin/plugin.json",
        json.dumps({"name": "x", "version": version}),
    )


def released(tmp_path, tag="v0.1.0"):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    manifest(root, "0.1.0")
    write(root, "scripts/core.py", "print(1)\n")
    write(root, "README.md", "readme\n")
    write(root, "CHANGELOG.md", CHANGELOG.format(unreleased=""))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    if tag:
        git(root, "tag", tag)
    return root


def commit(root):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")


def check(root):
    return subprocess.run(
        [sys.executable, str(TOOL)],
        check=False,
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_no_release_tag_yet_passes(tmp_path):
    root = released(tmp_path, tag=None)
    assert check(root).returncode == 0


def test_unchanged_since_the_tag_passes(tmp_path):
    root = released(tmp_path)
    assert check(root).returncode == 0


def test_docs_only_change_passes(tmp_path):
    root = released(tmp_path)
    write(root, "README.md", "better readme\n")
    commit(root)
    assert check(root).returncode == 0


def test_shipped_change_without_version_or_unreleased_entry_fails(tmp_path):
    root = released(tmp_path)
    write(root, "scripts/core.py", "print(2)\n")
    commit(root)
    result = check(root)
    assert result.returncode == 1
    assert "scripts/core.py" in result.stdout
    assert "0.1.0" in result.stdout


def test_shipped_change_with_unreleased_entry_passes(tmp_path):
    root = released(tmp_path)
    write(root, "scripts/core.py", "print(2)\n")
    write(root, "CHANGELOG.md", CHANGELOG.format(unreleased="\n- Faster core.\n"))
    commit(root)
    assert check(root).returncode == 0


def test_shipped_change_with_new_version_passes(tmp_path):
    root = released(tmp_path)
    write(root, "scripts/core.py", "print(2)\n")
    manifest(root, "0.2.0")
    commit(root)
    assert check(root).returncode == 0


def test_version_must_match_a_tag_when_released(tmp_path):
    root = released(tmp_path)
    manifest(root, "0.0.9")
    write(root, "scripts/core.py", "print(2)\n")
    commit(root)
    result = check(root)
    assert result.returncode == 1
    assert "0.0.9" in result.stdout


def test_uncommitted_changes_count_too(tmp_path):
    root = released(tmp_path)
    write(root, "skills/review/SKILL.md", "new\n")
    assert check(root).returncode == 1
