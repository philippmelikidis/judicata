"""Focus of a review: a part of the contract, never a verdict on all of it.

"Check for bugs" narrows a round to the correctness criteria and the heavy
finding categories. A focused round reports focus_clear or focus_open, never
done, and is marked in the ledger so the stop rule can skip it.
"""

import json

from conftest import judgment_contract, reviewer_output

HEAVY = "correctness,security,factual"


def focused_round(project, *focus: str) -> tuple[str, dict]:
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    started = project.ok("round", "start", *focus)
    return started["round"], started


def answer(*findings: dict, passed: bool = True) -> str:
    data = json.loads(reviewer_output("findings"))
    data["criteria"][0]["passed"] = passed
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = json.loads(reviewer_output("findings"))["findings"][0]
STYLE = {
    **BUG,
    "category": "style",
    "severity": "note",
    "summary": "Name is too short.",
    "evidence": "",
}


def submit_all(project, round_id: str, runs: list[dict], output: str) -> None:
    for run in runs:
        code, payload = project.submit(round_id, run, output)
        assert code == 0, payload


def test_focus_on_bugs_keeps_correctness_criteria_and_heavy_findings(project):
    round_id, started = focused_round(
        project, "--criteria", "JUD-1", "--categories", HEAVY
    )
    checked = project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    submit_all(project, round_id, plan["runs"], answer(STYLE))

    found = project.ok("review", "findings", "--round", round_id)["findings"]
    closed = project.ok("round", "close", "--round", round_id)

    assert [c["id"] for c in started["criteria"]] == ["JUD-1"]
    assert started["focus"] == {
        "criteria": ["JUD-1"],
        "categories": ["correctness", "security", "factual"],
    }
    assert checked["results"] == []  # the command criterion CODE-1 did not run
    assert [p["criterion"] for p in checked["pending"]] == ["JUD-1"]
    assert [c["id"] for c in plan["criteria"]] == ["JUD-1"]
    assert plan["focus"] == started["focus"]
    assert found == []  # the style note is outside the focus
    assert closed["outcome"] == "focus_clear"
    assert closed["focused"] is True
    assert closed["passed"] == ["JUD-1"]


def test_a_focused_round_never_reports_done_and_is_marked_in_the_ledger(project):
    round_id, _ = focused_round(project, "--criteria", "JUD-1", "--categories", HEAVY)
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    submit_all(project, round_id, plan["runs"], answer())

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] != "done"
    events = project.ledger_events()
    [started] = [e for e in events if e["type"] == "round_started"]
    [ended] = [e for e in events if e["type"] == "round_closed"]
    assert started["data"]["focus"]["criteria"] == ["JUD-1"]
    assert ended["data"]["focused"] is True
    assert ended["data"]["outcome"] == "focus_clear"


def test_a_confirmed_bug_in_focus_leaves_the_focus_open(project):
    round_id, _ = focused_round(project, "--criteria", "JUD-1", "--categories", HEAVY)
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    submit_all(project, round_id, plan["runs"], answer(BUG, STYLE))

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "focus_open"
    assert [f["category"] for f in closed["findings"]] == ["correctness"]
    [ended] = [e for e in project.ledger_events() if e["type"] == "round_closed"]
    assert ended["data"]["open_blockers"] == 1


def test_a_failed_criterion_in_focus_leaves_the_focus_open(project):
    round_id, _ = focused_round(project, "--criteria", "JUD-1", "--categories", "")
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    failed = json.loads(reviewer_output("fail"))
    submit_all(project, round_id, plan["runs"], json.dumps(failed))

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "focus_open"
    assert [b["criterion"] for b in closed["blockers"]] == ["JUD-1"]


def test_focus_on_commands_only_needs_no_reviewer(project):
    round_id, started = focused_round(
        project, "--criteria", "CODE-1", "--categories", ""
    )
    checked = project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)

    closed = project.ok("round", "close", "--round", round_id)

    assert started["focus"] == {"criteria": ["CODE-1"], "categories": []}
    assert [r["criterion"] for r in checked["results"]] == ["CODE-1"]
    assert plan["runs"] == []
    assert closed["outcome"] == "focus_clear"


def test_all_criteria_and_categories_are_no_focus(project):
    round_id, started = focused_round(project, "--criteria", "CODE-1,JUD-1")
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    submit_all(project, round_id, plan["runs"], answer())

    closed = project.ok("round", "close", "--round", round_id)

    assert started["focus"] is None
    assert closed["outcome"] == "done"
    assert closed["focused"] is False


def test_focus_on_categories_alone_keeps_every_criterion(project):
    _, started = focused_round(project, "--categories", "security")

    assert [c["id"] for c in started["criteria"]] == ["CODE-1", "JUD-1"]
    assert started["focus"] == {
        "criteria": ["CODE-1", "JUD-1"],
        "categories": ["security"],
    }


def test_unknown_criteria_categories_and_an_empty_focus_are_refused(project):
    project.write_contract("code", judgment_contract())

    unknown = project.fail("round", "start", "--criteria", "JUD-9")
    category = project.fail("round", "start", "--categories", "bugs")
    empty = project.fail("round", "start", "--criteria", "", "--categories", "")

    assert unknown["code"] == "criterion_not_found"
    assert unknown["details"]["criteria"] == ["CODE-1", "JUD-1"]
    assert category["code"] == "usage"
    assert empty["code"] == "empty_focus"
    assert not [e for e in project.ledger_events() if e["type"] == "round_started"]
