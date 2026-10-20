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


def test_a_change_to_the_prefixed_skill_copies_ships_too(tmp_path):
    root = released(tmp_path)
    write(root, ".agents/skills/judicata-review/SKILL.md", "new\n")
    commit(root)
    result = check(root)
    assert result.returncode == 1
    assert ".agents/skills/judicata-review/SKILL.md" in result.stdout


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


def test_moving_a_shipped_file_out_counts(tmp_path):
    root = released(tmp_path)
    (root / "docs").mkdir()
    git(root, "mv", "scripts/core.py", "docs/core.py")
    commit(root)
    assert check(root).returncode == 1


def test_a_newer_tag_on_another_branch_is_ignored(tmp_path):
    root = released(tmp_path)
    git(root, "checkout", "-q", "-b", "other")
    manifest(root, "0.2.0")
    commit(root)
    git(root, "tag", "v0.2.0")
    git(root, "checkout", "-q", "main")
    write(root, "README.md", "docs only\n")
    commit(root)
    assert check(root).returncode == 0


def test_a_path_with_spaces_is_not_split(tmp_path):
    root = released(tmp_path)
    write(root, "docs/release scripts/example.md", "x\n")
    commit(root)
    assert check(root).returncode == 0


def test_an_unreleased_section_with_only_headings_or_comments_is_empty(tmp_path):
    root = released(tmp_path)
    write(root, "scripts/core.py", "print(2)\n")
    write(
        root,
        "CHANGELOG.md",
        CHANGELOG.format(unreleased="\n### Added\n\n<!-- add entries here -->\n"),
    )
    commit(root)
    assert check(root).returncode == 1


def test_an_unreleased_entry_left_over_from_the_release_does_not_count(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    manifest(root, "0.1.0")
    write(root, "scripts/core.py", "print(1)\n")
    write(root, "CHANGELOG.md", CHANGELOG.format(unreleased="\n- Fix A.\n"))
    commit(root)
    git(root, "tag", "v0.1.0")
    write(root, "scripts/core.py", "print(2)\n")
    commit(root)
    assert check(root).returncode == 1


def test_a_prerelease_version_does_not_crash(tmp_path):
    root = released(tmp_path)
    write(root, "scripts/core.py", "print(2)\n")
    manifest(root, "0.2.0-rc.1")
    commit(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
