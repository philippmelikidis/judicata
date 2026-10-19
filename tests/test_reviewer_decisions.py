"""Reviewers get the user's decisions (with the reopen condition) in their task."""

import json

from conftest import judgment_contract, reviewer_output

REASON = ("--reason", "Callers never pass zero.")
REOPEN = ("--reopen-if", "ratio gets a caller that passes user input")


def output(*findings, fixture="findings") -> str:
    data = json.loads(reviewer_output(fixture))
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = json.loads(reviewer_output("findings"))["findings"][0]


def first_round(project, decision, *extra, fixture="findings", found=(BUG,)) -> dict:
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run in runs:
        code, payload = project.submit(round_id, run, output(*found, fixture=fixture))
        assert code == 0, payload
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", decision, *extra,
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)
    return entry


def plan(project, *start_args) -> dict:
    round_id = project.ok("round", "start", *start_args)["round"]
    project.ok("round", "check", "--round", round_id)
    return project.ok("review", "plan", "--round", round_id)


def test_the_plan_lists_rejected_findings_with_reason_and_reopen_condition(project):
    entry = first_round(project, "reject", *REASON, *REOPEN)

    shown = plan(project)

    assert shown["decisions"] == [
        {
            "fingerprint": entry["fingerprint"],
            "decision": "reject",
            "label": "src/shapes.py::ratio",
            "file": "src/shapes.py",
            "line": 21,
            "category": "correctness",
            "criterion": None,
            "summary": "ratio(1, 0) raises ZeroDivisionError.",
            "reason": REASON[1],
            "reopen_condition": REOPEN[1],
            "warnings": [],
        }
    ]
    assert shown["decisions_count"] == 1


def test_the_plan_lists_accepted_risks_too(project):
    first_round(project, "accept", *REASON, *REOPEN)

    [decision] = plan(project)["decisions"]

    assert decision["decision"] == "accept"


def test_a_fix_is_no_decision_for_the_reviewers(project):
    first_round(project, "fix")

    shown = plan(project)

    assert shown["decisions"] == []
    assert shown["decisions_count"] == 0


def test_a_decided_judgment_criterion_place_is_listed_with_its_criterion(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run in runs:
        project.submit(round_id, run, output(fixture="fail"))
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "accept", *REASON, *REOPEN,
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)

    [decision] = plan(project)["decisions"]

    assert decision["criterion"] == "JUD-1"
    assert decision["label"] == "src/app.py::average"
    assert decision["reopen_condition"] == REOPEN[1]


def test_a_narrowed_round_lists_only_decisions_in_its_files(project):
    first_round(project, "reject", *REASON, *REOPEN)  # in src/shapes.py

    assert plan(project, "--for", "src/app.py")["decisions"] == []
    assert len(plan(project, "--for", "src/shapes.py")["decisions"]) == 1


def test_a_focused_round_lists_only_decisions_in_its_categories(project):
    first_round(project, "reject", *REASON, *REOPEN)  # correctness

    assert plan(project, "--categories", "security")["decisions"] == []
