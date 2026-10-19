"""Severity and consensus of findings outside the contract, and their effect on "done"."""

import json

import pytest
from conftest import command_contract, judgment_contract, reviewer_output


def finding(**changes) -> dict:
    base = json.loads(reviewer_output("findings"))["findings"][0]
    return {**base, **changes}


def output(*findings) -> str:
    data = json.loads(reviewer_output("findings"))
    data["findings"] = list(findings)
    return json.dumps(data)


def reviewed(project, *per_run, plan=()) -> str:
    """A round where run i reports the findings in per_run[i]; returns the round id."""
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round(*plan)
    for run, findings in zip(runs, per_run, strict=True):
        code, payload = project.submit(round_id, run, output(*findings))
        assert code == 0, payload
    return round_id


def findings(project, round_id) -> list[dict]:
    return project.ok("review", "findings", "--round", round_id)["findings"]


BUG = finding()


def test_two_reviewers_with_the_same_blocker_confirm_it_and_block_done(project):
    round_id = reviewed(project, [BUG], [finding(summary="Division by zero.")])

    [entry] = findings(project, round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert entry["consensus"] == "confirmed"
    assert entry["severity"] == "blocker"
    assert entry["blocking"] is True
    assert len(entry["sightings"]) == 2
    assert closed["outcome"] == "continue"
    assert [f["fingerprint"] for f in closed["findings"]] == [entry["fingerprint"]]
    [event] = [e for e in project.ledger_events() if e["type"] == "round_closed"]
    assert event["data"]["open_blockers"] == 1


def test_a_blocker_only_one_of_two_reviewers_sees_is_unconfirmed(project):
    round_id = reviewed(project, [BUG], [])

    [entry] = findings(project, round_id)
    error = project.fail("round", "close", "--round", round_id)

    assert entry["consensus"] == "unconfirmed"
    assert entry["severity"] == "blocker"
    assert entry["blocking"] is False
    assert error["code"] == "decision_needed"  # the user confirms or rejects it


def test_both_reviewers_at_the_same_place_confirm_even_with_other_severities(
    project,
):
    round_id = reviewed(project, [BUG], [finding(severity="note")])

    [entry] = findings(project, round_id)

    assert entry["consensus"] == "confirmed"
    assert entry["severity"] == "blocker"
    assert entry["blocking"] is True
    assert sorted(s["proposed"] for s in entry["sightings"]) == ["blocker", "note"]


def test_a_confirmed_finding_without_evidence_on_either_side_stays_a_note(project):
    round_id = reviewed(project, [finding(evidence="")], [finding(severity="note")])

    [entry] = findings(project, round_id)

    assert (entry["consensus"], entry["severity"]) == ("confirmed", "note")


@pytest.mark.parametrize("severity", ["should-fix", "note"])
def test_other_single_sightings_become_notes(project, severity):
    round_id = reviewed(project, [finding(severity=severity)], [])

    [entry] = findings(project, round_id)

    assert entry["consensus"] == "single_sighting"
    assert entry["severity"] == "note"


def test_a_should_fix_both_reviewers_see_is_confirmed(project):
    should = finding(severity="should-fix")
    round_id = reviewed(project, [should], [should])

    [entry] = findings(project, round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert (entry["consensus"], entry["severity"]) == ("confirmed", "should-fix")
    assert closed["outcome"] == "done"


NEVER_BLOCKERS = {
    "no evidence": finding(evidence=""),
    "evidence left out": {k: v for k, v in BUG.items() if k != "evidence"},
    "light category": finding(category="style"),
    "other category": finding(category="other"),
}


@pytest.mark.parametrize("case", list(NEVER_BLOCKERS))
def test_a_finding_without_evidence_or_in_a_light_category_is_never_a_blocker(
    project, case
):
    item = NEVER_BLOCKERS[case]
    round_id = reviewed(project, [item], [item])

    [entry] = findings(project, round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert entry["consensus"] == "confirmed"
    assert entry["severity"] == "note"
    assert entry["blocking"] is False
    assert closed["outcome"] == "done"


@pytest.mark.parametrize("category", ["correctness", "security", "factual"])
def test_heavy_categories_with_evidence_may_block(project, category):
    item = finding(category=category)
    round_id = reviewed(project, [item], [item])

    [entry] = findings(project, round_id)

    assert entry["severity"] == "blocker"


def test_a_single_planned_reviewer_decides_directly(project):
    round_id = reviewed(project, [BUG], plan=("--count", "1"))

    [entry] = findings(project, round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert entry["consensus"] == "single_reviewer"
    assert entry["blocking"] is True
    assert closed["outcome"] == "continue"


def test_the_same_finding_twice_from_one_reviewer_is_not_a_confirmation(project):
    round_id = reviewed(project, [BUG, finding(summary="Again.")], [])

    [entry] = findings(project, round_id)

    assert entry["consensus"] == "unconfirmed"
    assert len(entry["sightings"]) == 1


def test_different_places_are_different_findings(project):
    other = finding(
        file="src/shapes.py",
        line=11,
        quote="return math.pi * self.radius**2",
        symbol="area",
    )
    round_id = reviewed(project, [BUG], [other])

    entries = findings(project, round_id)

    assert len(entries) == 2
    assert {e["consensus"] for e in entries} == {"unconfirmed"}


def test_closing_records_each_finding_as_an_event(project):
    round_id = reviewed(project, [BUG], [BUG])

    [entry] = findings(project, round_id)
    project.ok("round", "close", "--round", round_id)

    [event] = [e for e in project.ledger_events() if e["type"] == "finding_recorded"]
    data = event["data"]
    assert data["round"] == round_id
    assert data["contract"] == "code"
    assert data["fingerprint"] == entry["fingerprint"]
    assert data["section_hash"] == entry["section_hash"]
    assert data["address"] == entry["address"]
    assert (data["severity"], data["consensus"]) == ("blocker", "confirmed")


def test_findings_wait_until_every_run_is_in(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    project.submit(round_id, runs[0], output(BUG))

    payload = project.ok("review", "findings", "--round", round_id)

    assert payload["complete"] is False
    assert payload["findings"] == []


def test_a_contract_without_judgment_criteria_still_gets_a_reviewer_for_findings(
    project,
):
    project.add_artifact()
    project.write_contract("code", command_contract("true"))
    round_id, runs = project.judged_round()
    data = json.loads(output(BUG))
    data["criteria"] = []

    code, payload = project.submit(round_id, runs[0], json.dumps(data))
    closed = project.ok("round", "close", "--round", round_id)

    assert code == 0, payload
    assert len(runs) == 1
    assert closed["outcome"] == "continue"
    assert closed["findings"][0]["blocking"] is True


INVALID = {
    "unknown category": (finding(category="performance"), "category"),
    "unknown severity": (finding(severity="critical"), "severity"),
    "summary over two lines": (finding(summary="One.\nTwo."), "summary"),
    "quote not at the line": (finding(quote="return b / a"), "quote"),
    "file missing": (finding(file="src/nope.py"), "file"),
}


@pytest.mark.parametrize("case", list(INVALID))
def test_invalid_findings_reject_the_output(project, case):
    item, field = INVALID[case]
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    code, payload = project.submit(round_id, runs[0], output(item))

    assert code != 0
    assert payload["error"]["code"] == "invalid_reviewer_output"
    assert any(
        p["field"].startswith("findings[0]") and p["field"].endswith(field)
        for p in payload["error"]["details"]["problems"]
    ), payload["error"]["details"]["problems"]


def test_a_finding_outside_the_contract_scope_is_rejected(project):
    project.add_artifact()
    project.write_contract(
        "code", judgment_contract().replace('paths = ["**"]', 'paths = ["src/**"]')
    )
    round_id, runs = project.judged_round()
    item = finding(
        file="web/cart.js",
        line=2,
        quote="return items.reduce((sum, item) => sum + item.price, 0);",
    )

    code, payload = project.submit(round_id, runs[0], output(item))

    assert code != 0
    assert any(
        "scope" in p["problem"] for p in payload["error"]["details"]["problems"]
    ), payload


def test_a_finding_in_a_file_answered_for_another_contract_is_rejected(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    shapes = judgment_contract().replace('name = "code"', 'name = "shapes"')
    project.write_contract(
        "shapes", shapes.replace('paths = ["**"]', 'paths = ["src/shapes.py"]')
    )
    project.ok(
        "contract", "assign", "--for", "src/shapes.py", "--contract", "shapes",
        "--among", "shapes", "--among", "code",
    )  # fmt: skip
    round_id = project.ok("round", "start", "--contract", "code")["round"]
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)

    code, payload = project.submit(round_id, plan["runs"][0], output(BUG))

    assert plan["exclude"] == ["src/shapes.py"]
    assert code != 0
    assert any(
        p["field"] == "findings[0].file" and "scope" in p["problem"]
        for p in payload["error"]["details"]["problems"]
    ), payload
