"""Commands of a contract run only after this user approved them on this machine.

The approval is local (outside the project), keyed by the project path, the
contract name and the commands. Nothing in the repository can approve them.
"""

import json
import shutil

import pytest
from conftest import (
    Project,
    command_contract,
    judgment_contract,
    proposal,
    reviewer_output,
)

MARKER = "echo ran >> ran.txt"


def ran(project) -> int:
    marker = project.root / "ran.txt"
    return len(marker.read_text().splitlines()) if marker.exists() else 0


def check(project) -> tuple[str, dict]:
    round_id = project.ok("round", "start")["round"]
    return round_id, project.ok("round", "check", "--round", round_id)


def test_commands_nobody_approved_here_do_not_run_and_are_listed(project):
    project.write_contract("code", command_contract(MARKER, timeout=30), approve=False)

    round_id, checked = check(project)

    assert ran(project) == 0
    assert checked["results"] == []
    [pending] = checked["pending"]
    assert pending["criterion"] == "CODE-1"
    assert pending["command"] == MARKER
    assert "approval" in pending["reason"]
    approval = checked["approval"]
    assert approval["approved"] is False
    assert approval["contract"] == "code"
    assert approval["commands"] == [
        {"criterion": "CODE-1", "command": MARKER, "timeout": 30}
    ]
    closed = project.ok("round", "close", "--round", round_id)
    assert closed["outcome"] == "continue"
    assert [p["criterion"] for p in closed["pending"]] == ["CODE-1"]
    assert "approval" in closed["pending"][0]["reason"]


def test_approved_commands_run_in_this_round_and_in_later_ones(project):
    project.write_contract("code", command_contract(MARKER), approve=False)
    round_id, checked = check(project)

    approved = project.approve("code", checked["approval"]["commands_hash"])

    assert approved["approval"]["approved"] is True
    rerun = project.ok("round", "check", "--round", round_id)
    assert [r["criterion"] for r in rerun["results"]] == ["CODE-1"]
    assert rerun["pending"] == []
    assert rerun["approval"]["approved"] is True
    assert project.ok("round", "close", "--round", round_id)["outcome"] == "done"
    assert project.review()["outcome"] == "done"
    assert ran(project) == 2


def test_changed_commands_need_a_new_approval(project):
    project.write_contract("code", command_contract(MARKER))
    assert project.review()["outcome"] == "done"

    project.write_contract("code", command_contract(MARKER + " && true"), approve=False)
    _, checked = check(project)

    assert checked["results"] == []
    assert checked["approval"]["approved"] is False
    assert checked["approval"]["commands"][0]["command"] == MARKER + " && true"
    project.approve("code", checked["approval"]["commands_hash"])
    assert project.review()["outcome"] == "done"
    assert ran(project) == 2


def test_an_added_command_needs_a_new_approval_for_all_of_them(project):
    project.write_contract("code", command_contract(MARKER))
    added = (
        command_contract(MARKER)
        + '\n[[criteria]]\nid = "CODE-2"\ntext = "Lint"\ncheck = "command"\n'
        'required = true\ncommand = "true"\n'
    )
    project.write_contract("code", added, approve=False)

    _, checked = check(project)

    assert ran(project) == 0
    assert [c["criterion"] for c in checked["approval"]["commands"]] == [
        "CODE-1",
        "CODE-2",
    ]
    assert [p["criterion"] for p in checked["pending"]] == ["CODE-1", "CODE-2"]


def test_changing_only_the_text_of_a_criterion_keeps_the_approval(project):
    project.write_contract("code", command_contract(MARKER))
    reworded = command_contract(MARKER).replace("Tests pass", "The suite is green")
    project.write_contract("code", reworded, approve=False)

    assert project.review()["outcome"] == "done"
    assert ran(project) == 1


def test_a_created_contract_is_approved_for_its_author(project):
    project.ok("contract", "create", stdin=proposal(project))

    shown = project.ok("contract", "show")

    assert shown["approval"]["approved"] is True
    assert set(project.review()["passed"]) == {"CODE-1", "CODE-2"}


def test_the_checked_in_ledger_does_not_approve_for_another_user(project, tmp_path):
    project.ok("contract", "create", stdin=proposal(project))
    assert "approval_logged" in [e["type"] for e in project.ledger_events()]

    project.state_dir = tmp_path / "someone-else"
    _, checked = check(project)

    assert checked["results"] == []
    assert checked["approval"]["approved"] is False
    assert not (project.root / ".judicata" / "approvals").exists()


def test_an_approval_holds_only_for_the_project_it_was_given_in(project, tmp_path):
    project.write_contract("code", command_contract(MARKER))
    clone = Project(tmp_path / "clone", project.state_dir)
    shutil.copytree(project.root, clone.root)

    _, checked = check(clone)

    assert checked["approval"]["approved"] is False
    assert ran(clone) == 0


def test_approve_refuses_commands_other_than_the_ones_shown(project):
    project.write_contract("code", command_contract("true"), approve=False)
    _, checked = check(project)
    shown_hash = checked["approval"]["commands_hash"]
    project.write_contract("code", command_contract(MARKER), approve=False)

    error = project.fail(
        "contract", "approve", "--contract", "code", "--commands-hash", shown_hash
    )

    assert error["code"] == "approval_mismatch"
    assert error["details"]["commands"] == [
        {"criterion": "CODE-1", "command": MARKER, "timeout": 300}
    ]
    _, checked = check(project)
    assert checked["approval"]["approved"] is False


def test_a_contract_without_commands_needs_no_approval(project):
    text = command_contract("true").replace('check = "command"', 'check = "judgment"')
    text = text.replace('command = "true"\n', "")
    project.write_contract("code", text, approve=False)

    _, checked = check(project)

    assert checked["approval"] == {
        "contract": "code",
        "approved": True,
        "commands": [],
        "commands_hash": checked["approval"]["commands_hash"],
    }


def approval_files(directory) -> list:
    return sorted(directory.rglob("*.json")) if directory.exists() else []


@pytest.mark.parametrize(
    ("variables", "option", "expected"),
    [
        ({}, "opt", "opt"),
        ({"CLAUDE_PLUGIN_DATA": "plugin"}, None, "plugin"),
        ({"CLAUDE_PLUGIN_DATA": "", "XDG_STATE_HOME": "xdg"}, None, "xdg/judicata"),
        ({"CLAUDE_PLUGIN_DATA": "plugin"}, "", "plugin"),
        (
            {"CLAUDE_PLUGIN_DATA": "", "HOME": "home"},
            None,
            "home/.local/state/judicata",
        ),
    ],
)
def test_approvals_live_where_the_host_says_outside_the_project(
    project, tmp_path, variables, option, expected
):
    project.env = {"JUDICATA_STATE_DIR": None, "XDG_STATE_HOME": None}
    project.env.update({k: str(tmp_path / v) if v else v for k, v in variables.items()})
    extra = (
        []
        if option is None
        else ["--state-dir", str(tmp_path / option) if option else ""]
    )

    project.ok("contract", "create", *extra, stdin=proposal(project))

    assert len(approval_files(tmp_path / expected)) == 1
    assert approval_files(project.root) == []
    round_id = project.ok("round", "start")["round"]
    checked = project.ok("round", "check", "--round", round_id, *extra)
    assert checked["approval"]["approved"] is True


def test_a_state_dir_inside_the_project_is_refused(project):
    project.state_dir = project.root / "approvals"
    project.write_contract("code", command_contract(MARKER), approve=False)
    round_id = project.ok("round", "start")["round"]

    error = project.fail("round", "check", "--round", round_id)

    assert error["code"] == "state_dir_in_project"
    assert ran(project) == 0


@pytest.mark.parametrize("change", ["repeat", "reorder"])
def test_repeating_or_reordering_commands_needs_a_new_approval(project, change):
    second = '\n[[criteria]]\nid = "CODE-2"\ntext = "Again"\ncheck = "command"\n'
    second += 'required = true\ncommand = "true"\n'
    again = second.replace('"true"', json.dumps(MARKER))
    if change == "repeat":
        before, after = command_contract(MARKER), command_contract(MARKER) + again
    else:
        before, after = (
            command_contract(MARKER) + second,
            command_contract("true") + again,
        )
    project.write_contract("code", before)
    project.write_contract("code", after, approve=False)

    _, checked = check(project)

    assert checked["approval"]["approved"] is False
    assert ran(project) == 0


def test_an_approval_file_that_resolves_into_the_project_does_not_count(project):
    project.write_contract("code", command_contract(MARKER))
    [granted] = project.state_dir.rglob("*.json")
    planted = project.root / "planted.json"
    planted.write_bytes(granted.read_bytes())
    granted.unlink()
    granted.symlink_to(planted)

    _, checked = check(project)

    assert checked["approval"]["approved"] is False
    assert ran(project) == 0


def test_reviewers_still_judge_while_the_commands_wait_for_approval(project):
    project.add_artifact()
    text = judgment_contract(reviewers="count = 1").replace(
        'command = "true"', f"command = {json.dumps(MARKER)}"
    )
    project.write_contract("code", text, approve=False)

    round_id, [run] = project.judged_round()
    code, payload = project.submit(round_id, run, reviewer_output("pass"))
    closed = project.ok("round", "close", "--round", round_id)

    assert code == 0, payload
    assert ran(project) == 0
    assert closed["passed"] == ["JUD-1"]
    assert [p["criterion"] for p in closed["pending"]] == ["CODE-1"]
    assert "approval" in closed["pending"][0]["reason"]
    assert closed["outcome"] == "continue"


def test_a_state_dir_that_cannot_be_written_is_a_clear_error(project, tmp_path):
    # Codex runs the model's commands in a sandbox that may not write outside
    # the workspace: the skill needs a code it can act on, not a traceback.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    project.state_dir = locked / "state"
    try:
        error = project.fail("contract", "create", stdin=proposal(project))
        assert error["code"] == "state_dir_not_writable"
        assert error["details"]["state_dir"] == str(locked / "state")
        assert not (project.root / ".judicata" / "contracts" / "code.toml").exists()

        project.write_contract("code", command_contract(MARKER), approve=False)
        approval = project.ok("contract", "show")["approval"]
        error = project.fail(
            "contract", "approve", "--contract", "code",
            "--commands-hash", approval["commands_hash"],
        )  # fmt: skip
        assert error["code"] == "state_dir_not_writable"
    finally:
        locked.chmod(0o755)
