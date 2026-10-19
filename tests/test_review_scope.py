"""Scope of a review: a path, a module or the changes against a branch.

The skill turns "check the ledger module" or "review my changes against main"
into core options. scope diff and scope find are the deterministic parts;
round start --for/--diff narrows a round to those files, and every later step
(checks, reviewers) stays inside them.
"""

import json
import os
import subprocess

import pytest
from conftest import judgment_contract, reviewer_output


def git(project, *args: str, cwd=None) -> str:
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
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd or project.root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def write(project, path: str, text: str = "text\n") -> None:
    target = project.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(project, message: str) -> None:
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", message)


# Forbids TODO in every file of src/: shows which files a round looked at.
NO_TODO = """\
name = "code"
template = "code"
paths = ["src/**"]

[[criteria]]
id = "CODE-1"
text = "No TODO left"
check = "pattern"
required = true
forbid = "TODO"
"""


def hit_files(result: dict) -> list[str]:
    hits = [h for b in result["blockers"] for h in b["evidence"]["hits"]]
    return sorted({h["file"] for h in hits})


@pytest.fixture
def branch(project):
    """main with four files, then a branch feature that changes them."""
    git(project, "init", "-q")
    write(project, "src/kept.py", "x = 1  # TODO\n")
    write(project, "src/edited.py", "y = 1\n")
    write(project, "src/old_name.py", "def moved():\n    return 'a long enough body'\n")
    write(project, "src/gone.py", "z = 1\n")
    commit(project, "base")
    git(project, "checkout", "-q", "-b", "feature")
    write(project, "src/edited.py", "y = 2  # TODO\n")
    git(project, "mv", "src/old_name.py", "src/new_name.py")
    git(project, "rm", "-q", "src/gone.py")
    write(project, "src/added.py", "w = 1\n")
    commit(project, "feature")
    return project


def test_diff_lists_changed_files_with_renames_and_without_deleted_ones(branch):
    write(branch, "src/dirty.py", "uncommitted\n")  # untracked, not ignored

    result = branch.ok("scope", "diff", "--base", "main")

    assert result["files"] == [
        "src/added.py",
        "src/dirty.py",
        "src/edited.py",
        "src/new_name.py",
    ]
    assert result["file_count"] == 4
    assert result["renamed"] == [{"from": "src/old_name.py", "to": "src/new_name.py"}]
    assert result["deleted"] == ["src/gone.py"]
    assert result["base"] == "main"
    assert result["merge_base"] == git(branch, "rev-parse", "main")


def test_diff_includes_uncommitted_edits_and_skips_ignored_files(branch):
    write(branch, "src/kept.py", "x = 2  # TODO\n")
    write(branch, ".gitignore", "build/\n")
    write(branch, "build/out.py", "generated\n")

    files = branch.ok("scope", "diff", "--base", "main")["files"]

    assert "src/kept.py" in files
    assert ".gitignore" in files
    assert "build/out.py" not in files


def test_diff_starts_at_the_merge_base_not_at_the_tip_of_main(branch):
    git(branch, "checkout", "-q", "main")
    write(branch, "src/kept.py", "changed on main only\n")
    commit(branch, "main moves on")
    git(branch, "checkout", "-q", "feature")

    files = branch.ok("scope", "diff", "--base", "main")["files"]

    assert "src/kept.py" not in files
    assert "src/edited.py" in files


def test_diff_leaves_out_judicata_state(branch):
    branch.write_contract("code", NO_TODO)
    branch.review()

    files = branch.ok("scope", "diff", "--base", "main")["files"]

    assert not [f for f in files if f.startswith(".judicata/")]


def test_diff_paths_are_relative_to_a_project_inside_a_repository(tmp_path):
    from conftest import Project

    repo = tmp_path / "repo"
    (repo / "app" / "src").mkdir(parents=True)
    outer = Project(repo, tmp_path / "state")
    git(outer, "init", "-q")
    write(outer, "app/src/a.py", "a = 1\n")
    write(outer, "other/b.py", "b = 1\n")
    commit(outer, "base")
    git(outer, "checkout", "-q", "-b", "feature")
    write(outer, "app/src/a.py", "a = 2\n")
    write(outer, "other/b.py", "b = 2\n")
    commit(outer, "feature")
    inner = Project(repo / "app", tmp_path / "state")

    assert inner.ok("scope", "diff", "--base", "main")["files"] == ["src/a.py"]


def test_diff_against_an_unknown_base_or_without_git_is_an_error(branch, tmp_path):
    from conftest import Project

    error = branch.fail("scope", "diff", "--base", "trunk")
    assert error["code"] == "base_not_found"
    assert "main" in error["details"]["branches"]

    plain = tmp_path / "plain"
    plain.mkdir()
    error = Project(plain, tmp_path / "state").fail("scope", "diff")
    assert error["code"] == "not_a_git_repository"


def test_diff_uses_main_by_default(branch):
    assert branch.ok("scope", "diff")["base"] == "main"


def test_round_on_the_diff_checks_only_the_changed_files(branch):
    branch.write_contract("code", NO_TODO)

    whole = branch.review()
    narrowed = branch.review("--diff", "main")

    assert hit_files(whole) == ["src/edited.py", "src/kept.py"]
    assert hit_files(narrowed) == ["src/edited.py"]


def test_round_start_records_and_returns_the_scope(branch):
    branch.write_contract("code", NO_TODO)

    started = branch.ok("round", "start", "--diff", "main")

    assert started["scope"]["kind"] == "diff"
    assert started["scope"]["base"] == "main"
    assert started["scope"]["file_count"] == 3
    assert started["scope"]["files"] == [
        "src/added.py",
        "src/edited.py",
        "src/new_name.py",
    ]
    assert started["revision"]["files"] == 3
    [event] = [e for e in branch.ledger_events() if e["type"] == "round_started"]
    assert event["data"]["scope"]["kind"] == "diff"
    assert event["data"]["scope"]["base"] == "main"
    assert event["data"]["scope"]["merge_base"] == git(branch, "rev-parse", "main")


def test_a_round_without_narrowing_covers_the_contract(branch):
    branch.write_contract("code", NO_TODO)

    started = branch.ok("round", "start")

    assert started["scope"]["kind"] == "contract"
    assert started["scope"]["file_count"] == 4


def test_round_for_a_path_checks_only_the_files_below_it(project):
    project.write_contract("code", NO_TODO)
    write(project, "src/ledger/store.py", "a = 1  # TODO\n")
    write(project, "src/ledger/read.py", "b = 1\n")
    write(project, "src/cli.py", "c = 1  # TODO\n")

    result = project.review("--for", "src/ledger")

    assert hit_files(result) == ["src/ledger/store.py"]


def test_path_and_diff_together_take_the_changed_files_below_the_path(branch):
    branch.write_contract("code", NO_TODO)
    write(branch, "src/sub/x.py", "q = 1  # TODO\n")

    result = branch.review("--diff", "main", "--for", "src/sub")

    assert hit_files(result) == ["src/sub/x.py"]


def test_a_scope_without_files_of_the_contract_is_refused(project):
    project.write_contract("code", NO_TODO)
    write(project, "src/app.py", "a = 1\n")
    write(project, "docs/guide.md", "text\n")

    error = project.fail("round", "start", "--for", "docs")

    assert error["code"] == "empty_scope"
    assert project.fail("round", "start", "--for", "nowhere")["code"] == (
        "path_not_found"
    )


def test_resolve_on_the_diff_groups_only_the_changed_files(branch):
    branch.write_contract("code", NO_TODO)

    result = branch.ok("contract", "resolve", "--diff", "main")

    [group] = result["resolved"]
    assert group["contract"] == "code"
    assert group["files"] == ["src/added.py", "src/edited.py", "src/new_name.py"]
    assert result["diff"]["base"] == "main"


def test_reviewers_get_the_narrowed_scope_and_must_stay_inside_it(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = project.ok("round", "start", "--for", "src/app.py")["round"]
    project.ok("round", "check", "--round", round_id)

    plan = project.ok("review", "plan", "--round", round_id)
    findings = json.loads(reviewer_output("findings"))  # a finding in src/shapes.py
    code, payload = project.submit(round_id, plan["runs"][0], json.dumps(findings))

    assert plan["scope"]["kind"] == "paths"
    assert plan["scope"]["paths"] == ["src/app.py"]
    assert plan["scope"]["files"] == ["src/app.py"]
    assert code != 0
    assert payload["error"]["code"] == "invalid_reviewer_output"
    [problem] = payload["error"]["details"]["problems"]
    assert problem["field"] == "findings[0].file"


def test_find_names_the_files_and_directories_of_a_module(project):
    write(project, "scripts/core/ledger.py")
    write(project, "scripts/core/ledger_test_data.txt")
    write(project, "tests/test_ledger.py")
    write(project, "pkg/Ledger/__init__.py")
    write(project, "docs/other.md")

    result = project.ok("scope", "find", "--name", "ledger")

    assert result["matches"] == [
        {"path": "pkg/Ledger", "kind": "directory", "file_count": 1},
        {"path": "scripts/core/ledger.py", "kind": "file", "file_count": 1},
    ]


def test_find_takes_dotted_and_slashed_module_paths(project):
    write(project, "scripts/core/ledger.py")
    write(project, "lib/ledger.py")

    dotted = project.ok("scope", "find", "--name", "core.ledger")["matches"]
    slashed = project.ok("scope", "find", "--name", "core/ledger")["matches"]
    with_suffix = project.ok("scope", "find", "--name", "ledger.py")["matches"]

    assert [m["path"] for m in dotted] == ["scripts/core/ledger.py"]
    assert [m["path"] for m in slashed] == ["scripts/core/ledger.py"]
    assert [m["path"] for m in with_suffix] == [
        "lib/ledger.py",
        "scripts/core/ledger.py",
    ]


def test_find_without_a_match_returns_nothing(project):
    write(project, "src/app.py")

    assert project.ok("scope", "find", "--name", "ledger")["matches"] == []


def test_reviewers_of_a_diff_get_the_patch_with_removed_lines_and_renames(branch):
    branch.write_contract("code", judgment_contract().replace('"**"', '"src/**"'))
    write(branch, "src/new.py", "brand new\n")  # untracked: no patch, a new file
    round_id = branch.ok("round", "start", "--diff", "main")["round"]

    plan = branch.ok("review", "plan", "--round", round_id)
    patch = plan["scope"]["patch"]

    assert "-y = 1" in patch
    assert "+y = 2  # TODO" in patch
    assert "rename from src/old_name.py" in patch
    assert "src/kept.py" not in patch
    assert plan["scope"]["untracked"] == ["src/new.py"]


def test_resolve_on_the_diff_counts_the_deleted_files(branch):
    branch.write_contract("code", NO_TODO)

    result = branch.ok("contract", "resolve", "--diff", "main")

    assert result["diff"]["deleted"] == ["src/gone.py"]


def test_a_decision_about_other_files_does_not_carry_into_a_narrowed_round(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    first = project.ok("round", "start", "--for", "src/app.py")["round"]
    project.ok("round", "check", "--round", first)
    runs = project.ok("review", "plan", "--round", first)["runs"]
    project.submit(first, runs[0], reviewer_output("pass"))
    project.submit(first, runs[1], reviewer_output("fail"))
    project.ok(
        "review", "decide", "--round", first, "--criterion", "JUD-1",
        "--passed", "false", "--reason", "average([]) crashes",
    )  # fmt: skip
    project.ok("round", "close", "--round", first)

    second = project.ok("round", "start", "--for", "src/shapes.py")["round"]
    project.ok("round", "check", "--round", second)
    runs = project.ok("review", "plan", "--round", second)["runs"]
    clean = json.loads(reviewer_output("pass"))
    clean["criteria"][0]["evidence"] = []
    for run in runs:
        project.submit(second, run, json.dumps(clean))

    [entry] = project.ok("review", "verdicts", "--round", second)["criteria"]

    assert entry["status"] == "agreed"
    assert entry["passed"] is True
