"""Command criteria: exit code 0 passes, anything else is evidence for a blocker."""

from conftest import command_contract


def test_passing_required_command_makes_the_round_done(project):
    project.write_contract("code", command_contract("true"))

    result = project.review()

    assert result["outcome"] == "done"
    assert result["blockers"] == []


def test_failing_required_command_is_a_blocker_with_the_last_output_lines(project):
    script = "for i in $(seq 1 100); do echo line $i; done; echo broken >&2; exit 3"
    project.write_contract("code", command_contract(script))

    result = project.review()

    assert result["outcome"] == "continue"
    [blocker] = result["blockers"]
    assert blocker["criterion"] == "CODE-1"
    assert blocker["severity"] == "blocker"
    evidence = blocker["evidence"]
    assert evidence["exit_code"] == 3
    assert evidence["timed_out"] is False
    tail = evidence["output_tail"].splitlines()
    assert tail[-1] == "broken"
    assert "line 100" in tail
    assert "line 1" not in tail
    assert len(tail) <= 20


def test_command_that_runs_too_long_is_stopped_and_fails(project):
    project.write_contract(
        "code", command_contract("echo started; sleep 30; echo finished", timeout=1)
    )

    round_id = project.ok("round", "start")["round"]
    checked = project.ok("round", "check", "--round", round_id)

    [result] = checked["results"]
    assert result["passed"] is False
    assert result["evidence"]["timed_out"] is True
    assert result["evidence"]["exit_code"] is None
    assert result["evidence"]["duration_seconds"] < 10
    assert "started" in result["evidence"]["output_tail"]
    assert "finished" not in result["evidence"]["output_tail"]
    closed = project.ok("round", "close", "--round", round_id)
    assert closed["outcome"] == "continue"
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-1"]


def test_command_runs_in_the_project_directory(project):
    project.write_contract("code", command_contract("test -f marker.txt"))
    assert project.review()["outcome"] == "continue"

    (project.root / "marker.txt").write_text("here\n")
    assert project.review()["outcome"] == "done"


def test_failing_optional_command_is_should_fix_and_does_not_block(project):
    project.write_contract("code", command_contract("exit 1", required=False))

    result = project.review()

    assert result["outcome"] == "done"
    assert result["blockers"] == []
    assert [s["criterion"] for s in result["should_fix"]] == ["CODE-1"]
    assert result["should_fix"][0]["severity"] == "should-fix"


def test_required_judgment_criterion_stays_pending_and_keeps_the_round_open(project):
    project.write_contract(
        "code",
        command_contract("true")
        + '\n[[criteria]]\nid = "CODE-2"\ntext = "No known bugs"\n'
        'check = "judgment"\nrequired = true\n',
    )

    result = project.review()

    assert result["outcome"] == "continue"
    assert result["passed"] == ["CODE-1"]
    assert [p["criterion"] for p in result["pending"]] == ["CODE-2"]


def test_huge_output_still_yields_the_last_lines(project):
    project.write_contract("code", command_contract("seq 1 3000000; exit 1"))

    [blocker] = project.review()["blockers"]

    tail = blocker["evidence"]["output_tail"].splitlines()
    assert tail[-1] == "3000000"
    assert tail[0] == "2999981"
