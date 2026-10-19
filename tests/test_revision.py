"""Every round records the revision of the artifact; judicata also works without Git.

Revision: the content hash of the files in the contract's paths, plus the
commit SHA when the project is in a Git repository with a commit.
"""

import os
import subprocess

import pytest
from conftest import command_contract, proposal

SRC_CONTRACT = """\
name = "code"
template = "code"
paths = ["src/**"]

[[criteria]]
id = "CODE-1"
text = "Tests pass"
check = "command"
required = true
command = "true"
"""


def git(project, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=project.root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def revision(project) -> dict:
    return project.ok("round", "start")["revision"]


def write(project, path: str, text: str) -> None:
    target = project.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def no_git(monkeypatch, tmp_path):
    """No git executable anywhere on PATH."""
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def test_a_full_review_runs_without_git(project, no_git):
    write(project, "src/app.py", "print('hi')\n")
    draft = proposal(project)
    draft["criteria"] = [c for c in draft["criteria"] if c["check"] == "command"]
    project.ok("contract", "create", stdin=draft)

    started = project.ok("round", "start")
    project.ok("round", "check", "--round", started["round"])
    closed = project.ok("round", "close", "--round", started["round"])

    assert started["revision"]["commit"] is None
    assert len(started["revision"]["content"]) == 64
    assert closed["outcome"] == "done"
    assert not (project.root / ".git").exists()


def test_the_revision_without_git_is_the_content_hash_of_the_scope(project, no_git):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/app.py", "print('hi')\n")
    write(project, "README.md", "hello\n")
    first = revision(project)

    assert revision(project) == first  # the growing ledger is not part of it
    write(project, "README.md", "changed, but outside the paths\n")
    assert revision(project) == first
    write(project, "src/app.py", "print('changed')\n")
    changed = revision(project)
    write(project, "src/app.py", "print('hi')\n")

    assert changed["content"] != first["content"]
    assert revision(project) == first
    assert first == {"commit": None, "content": first["content"], "files": 1}


def test_a_new_or_renamed_file_changes_the_revision(project, no_git):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/a.py", "x = 1\n")
    first = revision(project)

    (project.root / "src" / "a.py").rename(project.root / "src" / "b.py")
    renamed = revision(project)
    write(project, "src/c.py", "")
    added = revision(project)

    assert len({first["content"], renamed["content"], added["content"]}) == 3
    assert added["files"] == 2


def test_with_git_the_revision_adds_the_commit_sha(project):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/app.py", "print('hi')\n")
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "start")

    with_git = revision(project)
    (project.root / ".git").rename(project.root / ".git-away")
    without_git = revision(project)

    assert with_git["commit"] == git(
        project, "--git-dir=.git-away", "rev-parse", "HEAD"
    )
    assert without_git["commit"] is None
    assert with_git["content"] == without_git["content"]


def test_a_repository_without_commits_has_only_the_content_hash(project):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/app.py", "print('hi')\n")
    git(project, "init", "-q")

    assert revision(project)["commit"] is None


def test_files_git_ignores_are_not_part_of_the_revision(project):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/app.py", "print('hi')\n")
    write(project, ".gitignore", "*.log\n")
    git(project, "init", "-q")
    first = revision(project)

    write(project, "src/debug.log", "noise\n")

    assert revision(project) == first


def test_the_revision_is_recorded_with_the_round(project):
    project.write_contract("code", command_contract("true"))
    started = project.ok("round", "start")

    recorded = [
        e["data"] for e in project.ledger_events() if e["type"] == "round_started"
    ]
    assert recorded[-1]["revision"] == started["revision"]


def test_a_nested_repository_does_not_bring_its_git_files_in(project, no_git):
    project.write_contract("code", command_contract("true"))
    write(project, "vendor/lib/app.py", "x = 1\n")
    write(project, "vendor/lib/.git/config", "[core]\n")
    first = revision(project)

    write(project, "vendor/lib/.git/config", "[core]\n\tbare = false\n")

    assert revision(project) == first
    assert first["files"] == 2  # .gitattributes and vendor/lib/app.py


def test_each_check_records_the_revision_it_ran_against(project, no_git):
    project.write_contract("code", SRC_CONTRACT)
    write(project, "src/app.py", "print('hi')\n")
    started = project.ok("round", "start")
    write(project, "src/app.py", "print('fixed before the check')\n")

    checked = project.ok("round", "check", "--round", started["round"])

    assert checked["revision"]["content"] != started["revision"]["content"]
    results = [
        e["data"] for e in project.ledger_events() if e["type"] == "criterion_result"
    ]
    assert [r["revision"] for r in results] == [checked["revision"]]
