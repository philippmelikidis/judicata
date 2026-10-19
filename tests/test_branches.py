"""Two branches review in parallel: Git merges the ledger without conflicts.

Every test runs real Git in a throwaway repository. After the merge each event
counts once and the state is the same whichever branch merged the other.
"""

import json
import os
import subprocess

import pytest
from conftest import command_contract

# passes on branches that have ok.txt, fails on the others
NEEDS_OK = command_contract("test -f ok.txt")


def git(project, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
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
        cwd=project.root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def commit(project, message: str) -> None:
    git(project, "add", "-A")
    result = git(project, "commit", "-q", "-m", message)
    assert result.returncode == 0, result.stderr


def merge(project, branch: str) -> None:
    result = git(project, "merge", "--no-edit", branch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "<<<<<<<" not in project.ledger.read_text(encoding="utf-8")


def checkout(project, *args: str) -> None:
    result = git(project, "checkout", "-q", *args)
    assert result.returncode == 0, result.stderr


def round_is_known(project, round_id: str) -> bool:
    code = project.fail("round", "close", "--round", round_id)["code"]
    return code == "round_closed"


@pytest.fixture
def repo(project):
    if git(project, "--version").returncode != 0:
        pytest.skip("git is not installed")
    assert git(project, "init", "-q").returncode == 0
    return project


def test_new_ledger_lines_on_two_branches_merge_without_conflict(repo):
    repo.write_contract("code", NEEDS_OK)
    first = repo.review()["round"]
    commit(repo, "contract and first review")

    checkout(repo, "-b", "feature")
    (repo.root / "ok.txt").write_text("ok\n", encoding="utf-8")
    on_feature = repo.review()
    commit(repo, "review on feature")

    checkout(repo, "main")
    on_main = repo.review()
    commit(repo, "review on main")

    merge(repo, "feature")

    assert on_feature["outcome"] == "done"
    assert on_main["outcome"] == "continue"
    for round_id in (first, on_feature["round"], on_main["round"]):
        assert round_is_known(repo, round_id)
    ids = [e["id"] for e in repo.ledger_events()]
    # 3 rounds of 3 events, plus the finding of each of the 2 failed rounds
    assert len(ids) == len(set(ids)) == 11
    assert repo.review()["outcome"] == "done"  # merged tree has ok.txt


def test_branches_that_both_start_the_ledger_merge_without_conflict(repo):
    (repo.root / "README.md").write_text("hello\n", encoding="utf-8")
    commit(repo, "start")

    checkout(repo, "-b", "feature")
    repo.write_contract("code", NEEDS_OK)
    on_feature = repo.review()["round"]
    commit(repo, "first review on feature")

    checkout(repo, "main")
    repo.write_contract("code", NEEDS_OK)
    on_main = repo.review()["round"]
    commit(repo, "first review on main")

    merge(repo, "feature")

    assert round_is_known(repo, on_feature)
    assert round_is_known(repo, on_main)
    # 2 failed rounds: start, result, finding, close each
    assert len(repo.ledger_events()) == 8


def test_a_cherry_picked_ledger_commit_counts_once_after_the_merge(repo):
    repo.write_contract("code", command_contract("echo run >> ../runs.txt; false"))
    repo.review()
    commit(repo, "contract and first review")

    checkout(repo, "-b", "feature")
    round_id = repo.ok("round", "start")["round"]
    repo.ok("round", "check", "--round", round_id)
    commit(repo, "round on feature")
    picked = git(repo, "rev-parse", "HEAD").stdout.strip()
    repo.review()
    commit(repo, "another review on feature")

    checkout(repo, "main")
    repo.review()
    commit(repo, "review on main")
    assert git(repo, "cherry-pick", picked).returncode == 0

    merge(repo, "feature")

    ids = [e["id"] for e in repo.ledger_events()]
    assert len(ids) > len(set(ids)), "the merge should have duplicated lines"
    runs = (repo.root.parent / "runs.txt").read_text().splitlines()
    rerun = repo.ok("round", "check", "--round", round_id)
    closed = repo.ok("round", "close", "--round", round_id)
    assert len(rerun["results"]) == 1
    assert (repo.root.parent / "runs.txt").read_text().splitlines() == runs
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-1"]


def test_the_state_is_the_same_whichever_branch_merges_the_other(repo):
    repo.write_contract("code", NEEDS_OK)
    round_id = repo.ok("round", "start")["round"]
    commit(repo, "round started")

    checkout(repo, "-b", "a")
    (repo.root / "ok.txt").write_text("ok\n", encoding="utf-8")
    repo.ok("round", "check", "--round", round_id)  # passes, and is checked first
    commit(repo, "checked on a")

    checkout(repo, "-b", "b", "main")
    repo.ok("round", "check", "--round", round_id)  # fails
    commit(repo, "checked on b")

    outcomes = {}
    for ours, theirs in (("a", "b"), ("b", "a")):
        checkout(repo, "-b", f"{ours}-merges-{theirs}", ours)
        merge(repo, theirs)
        closed = repo.ok("round", "close", "--round", round_id)
        outcomes[ours] = (closed["outcome"], closed["passed"], closed["blockers"])
        git(repo, "checkout", "-q", "--", ".judicata/ledger.jsonl")
        assert git(repo, "status", "--porcelain").stdout == ""

    assert outcomes["a"] == outcomes["b"]
    assert outcomes["a"][0] == "done"  # the first check counts on both


def test_events_written_after_merging_a_clock_ahead_still_apply(project):
    """A branch from a machine whose clock runs ahead must not hide later events."""
    project.write_contract("code", command_contract("echo run >> runs.txt; false"))
    round_id = project.ok("round", "start")["round"]
    events = project.ledger_events()
    for event in events:
        event["ts"] = "2099-01-01T00:00:00.000000Z"
    project.ledger.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )

    project.ok("round", "check", "--round", round_id)
    project.ok("round", "check", "--round", round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert (project.root / "runs.txt").read_text().splitlines() == ["run"]
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-1"]


def test_a_decision_on_a_criterion_changed_on_another_branch_lapses_after_merge(
    project,
):
    contract = {
        "name": "code",
        "template": "code",
        "paths": ["src/**"],
        "criteria": [
            {
                "id": "PAT-1",
                "text": "No TODO left in the code",
                "check": "pattern",
                "required": True,
                "forbid": "TODO",
            }
        ],
    }
    assert git(project, "init", "-q").returncode == 0
    (project.root / "src").mkdir()
    (project.root / "src" / "app.py").write_text("x = 1  # TODO\n", encoding="utf-8")
    project.ok("contract", "create", stdin=contract)
    commit(project, "contract")
    checkout(project, "-b", "a")
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    commit(project, "round started on a")
    checkout(project, "-b", "b", "main")
    changed = json.loads(json.dumps(contract))
    changed["criteria"][0]["text"] = "No TODO anywhere"
    shown = project.ok("contract", "change", stdin=changed)
    project.ok("contract", "change", "--confirm", shown["change_hash"], stdin=changed)
    commit(project, "contract changed on b")
    checkout(project, "a")
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "reject", "--reason", "on purpose",
        "--reopen-if", "never",
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)
    commit(project, "decided on a")

    merge(project, "b")

    listed = project.ok("finding", "list", "--contract", "code")
    assert listed["register"] == []
    [finding] = listed["findings"]
    assert finding["state"] == "closed"
    assert finding["decision"] is None
