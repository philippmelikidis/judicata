"""A new session judges like the last one: a rejected or accepted finding at an
unchanged place is only counted as a sighting, at a changed place it comes back
to the user with the old decision (re-presentation, "keep" is an option)."""

import json

import pytest
from conftest import judgment_contract, reviewer_output

REASON = ("--reason", "Callers never pass zero.")
REOPEN = ("--reopen-if", "ratio gets a caller that passes user input")


def finding(**changes) -> dict:
    base = json.loads(reviewer_output("findings"))["findings"][0]
    return {**base, **changes}


def output(*findings) -> str:
    data = json.loads(reviewer_output("findings"))
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = finding()  # correctness blocker in src/shapes.py::ratio
NOTE = finding(category="style", severity="note", summary="ratio is terse.")


def judged(project, *per_run) -> str:
    round_id, runs = project.judged_round()
    for run, found in zip(runs, per_run, strict=True):
        code, payload = project.submit(round_id, run, output(*found))
        assert code == 0, payload
    return round_id


def listing(project, round_id) -> dict:
    return project.ok("finding", "list", "--round", round_id)


def decide(project, round_id, fingerprint, decision, *extra) -> dict:
    return project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint", fingerprint,
        "--decision", decision, *extra,
    )  # fmt: skip


def close(project, round_id) -> dict:
    return project.ok("round", "close", "--round", round_id)


def decided_first(project, decision="reject", found=BUG) -> dict:
    """Round 1: both reviewers report the finding, the user decides it."""
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = judged(project, [found], [found])
    [entry] = listing(project, round_id)["findings"]
    decide(project, round_id, entry["fingerprint"], decision, *REASON, *REOPEN)
    close(project, round_id)
    return entry


def edit(project, old: str, new: str, file="src/shapes.py") -> None:
    path = project.root / file
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


CHANGE_RATIO = (  # changes the place, the quote stays at its line
    "    return a / b\n",
    "    return a / b  # b is checked by the callers\n",
)
CHANGE_AREA = ("self.radius**2", "self.radius ** 2")


def counts(project) -> dict:
    return project.ok("finding", "list", "--contract", "code")["counts"]


# suppression


@pytest.mark.parametrize(
    ("decision", "state"), [("reject", "rejected"), ("accept", "accepted")]
)
def test_a_decided_finding_at_an_unchanged_place_is_not_put_to_the_user(
    project, decision, state
):
    first = decided_first(project, decision)

    round_id = judged(project, [BUG], [BUG])
    listed = listing(project, round_id)
    closed = close(project, round_id)

    assert listed["findings"] == []
    assert listed["decisions_needed"] == []
    [quiet] = listed["suppressed"]
    assert quiet["fingerprint"] == first["fingerprint"]
    assert quiet["state"] == state
    assert quiet["decision"]["reason"] == REASON[1]
    assert closed["outcome"] == "done"
    assert closed["findings"] == []
    assert [s["fingerprint"] for s in closed["suppressed"]] == [first["fingerprint"]]


def test_a_suppressed_finding_still_counts_as_a_sighting(project):
    first = decided_first(project)

    close(project, judged(project, [BUG], [BUG]))
    [item] = project.ok("finding", "list", "--contract", "code")["findings"]

    assert item["fingerprint"] == first["fingerprint"]
    assert item["rounds_seen"] == 2
    assert item["state"] == "rejected"


def test_a_suppressed_sighting_does_not_change_the_decided_finding(project):
    # Found by eval case 1 (#18): the next reviewer saw the rejected blocker
    # again as a note; the round then said a plain "done" instead of "done with
    # accepted risks", and the register showed the finding as a note.
    decided_first(project)
    lighter = finding(severity="note", summary="ratio may divide by zero.")

    closed = close(project, judged(project, [lighter], [lighter]))
    [item] = project.ok("finding", "list", "--contract", "code")["findings"]

    assert closed["outcome_label"] == "done with accepted risks"
    assert [r["kind"] for r in closed["accepted_risks"]] == ["finding"]
    assert item["severity"] == "blocker"
    assert item["summary"] == BUG["summary"]
    assert item["rounds_seen"] == 2


def test_a_change_elsewhere_in_the_file_does_not_bring_it_back(project):
    decided_first(project)
    edit(project, *CHANGE_AREA)

    round_id = judged(project, [BUG], [BUG])
    listed = listing(project, round_id)

    assert listed["findings"] == []
    assert len(listed["suppressed"]) == 1


def test_a_decided_criterion_finding_at_an_unchanged_place_is_suppressed(project):
    project.write_contract(
        "code",
        'name = "code"\ntemplate = "code"\npaths = ["src/**"]\n\n[[criteria]]\n'
        'id = "PAT-1"\ntext = "No TODO"\ncheck = "pattern"\nrequired = true\n'
        'forbid = "TODO"\n',
    )
    (project.root / "src").mkdir()
    todo = project.root / "src" / "todo.py"
    todo.write_text("def first():\n    return 1  # TODO\n", encoding="utf-8")
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [entry] = listing(project, round_id)["findings"]
    decide(project, round_id, entry["fingerprint"], "accept", *REASON, *REOPEN)
    close(project, round_id)

    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    listed = listing(project, round_id)
    closed = close(project, round_id)

    assert listed["findings"] == []
    assert [s["fingerprint"] for s in listed["suppressed"]] == [entry["fingerprint"]]
    # a required criterion is never quietly done (#33)
    assert closed["outcome"] == "required_unmet"
    [unmet] = closed["required_unmet"]
    assert unmet["criterion"] == "PAT-1"


# re-presentation


def test_a_decided_finding_at_a_changed_place_comes_back_with_the_old_decision(
    project,
):
    first = decided_first(project)
    edit(project, *CHANGE_RATIO)

    round_id = judged(project, [BUG], [BUG])
    listed = listing(project, round_id)

    [entry] = listed["findings"]
    assert entry["fingerprint"] == first["fingerprint"]
    assert entry["presentation"] == "represented"
    assert entry["state"] == "rejected"
    assert entry["needs_decision"] is True
    assert entry["previous_decision"]["decision"] == "reject"
    assert entry["previous_decision"]["reason"] == REASON[1]
    assert entry["previous_decision"]["reopen_condition"] == REOPEN[1]
    assert entry["section_hash"] != first["section_hash"]
    assert listed["decisions_needed"] == [first["fingerprint"]]
    assert listed["suppressed"] == []


def test_a_re_presented_blocker_must_be_decided_before_close(project):
    first = decided_first(project)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])

    error = project.fail("round", "close", "--round", round_id)

    assert error["code"] == "decision_needed"
    assert error["details"]["findings"] == [first["fingerprint"]]


@pytest.mark.parametrize(
    ("decision", "state"), [("reject", "rejected"), ("accept", "accepted")]
)
def test_keep_holds_the_old_decision_for_the_changed_place(project, decision, state):
    first = decided_first(project, decision)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])

    [before] = listing(project, round_id)["findings"]
    kept = decide(project, round_id, first["fingerprint"], "keep")
    [entry] = listing(project, round_id)["findings"]
    closed = close(project, round_id)

    assert kept["decision"] == decision
    assert kept["state"] == state
    assert entry["needs_decision"] is False
    assert entry["decision"]["reason"] == REASON[1]
    assert entry["decision"]["reopen_condition"] == REOPEN[1]
    assert entry["decision"]["section_hash"] == entry["section_hash"]
    assert closed["outcome"] == "done"
    [event] = [
        e
        for e in project.ledger_events()
        if e["type"] == "finding_decided" and e["data"]["round"] == round_id
    ]
    assert event["data"]["decision"] == decision
    assert event["data"]["kept"] == before["previous_decision"]["event"]


def test_after_keep_the_changed_place_is_suppressed_in_the_next_round(project):
    decided_first(project)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])
    [entry] = listing(project, round_id)["findings"]
    decide(project, round_id, entry["fingerprint"], "keep")
    close(project, round_id)

    listed = listing(project, judged(project, [BUG], [BUG]))

    assert listed["findings"] == []
    assert len(listed["suppressed"]) == 1


def test_keep_may_take_a_new_reason(project):
    first = decided_first(project)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])

    decide(
        project, round_id, first["fingerprint"], "keep",
        "--reason", "Still only internal callers.",
    )  # fmt: skip
    [entry] = listing(project, round_id)["findings"]

    assert entry["decision"]["reason"] == "Still only internal callers."
    assert entry["decision"]["reopen_condition"] == REOPEN[1]


def test_a_re_presented_finding_can_be_decided_anew(project):
    first = decided_first(project)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])

    decide(project, round_id, first["fingerprint"], "fix")
    closed = close(project, round_id)

    assert closed["outcome"] == "continue"
    assert counts(project)["resolved"] == 1


def test_keep_needs_a_re_presented_finding(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = judged(project, [BUG], [BUG])
    [entry] = listing(project, round_id)["findings"]

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "keep",
    )  # fmt: skip

    assert error["code"] == "not_represented"


def test_a_suppressed_finding_takes_no_decision(project):
    first = decided_first(project)
    round_id = judged(project, [BUG], [BUG])

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint",
        first["fingerprint"], "--decision", "keep",
    )  # fmt: skip

    assert error["code"] == "not_represented"


def test_a_re_presented_note_is_shown_but_does_not_hold_up_the_round(project):
    first = decided_first(project, found=NOTE)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [NOTE], [NOTE])

    [entry] = listing(project, round_id)["findings"]
    closed = close(project, round_id)

    assert entry["fingerprint"] == first["fingerprint"]
    assert entry["presentation"] == "represented"
    assert entry["needs_decision"] is False
    assert closed["outcome"] == "done"
    assert counts(project)["rejected"] == 1


def test_a_re_presented_finding_can_be_kept_after_the_round_closed(project):
    first = decided_first(project, found=NOTE)
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [NOTE], [NOTE])
    close(project, round_id)

    decide(project, round_id, first["fingerprint"], "keep")
    listed = listing(project, judged(project, [NOTE], [NOTE]))

    assert listed["findings"] == []
    assert len(listed["suppressed"]) == 1


def test_a_fix_on_a_re_presented_finding_takes_its_new_severity(project):
    first = decided_first(project, "accept", found=finding(severity="note"))
    edit(project, *CHANGE_RATIO)
    round_id = judged(project, [BUG], [BUG])

    decide(project, round_id, first["fingerprint"], "fix")
    closed = close(project, round_id)

    assert closed["outcome"] == "continue"
    assert closed["open_blockers"] == 1


def test_an_older_round_does_not_ask_again_after_a_newer_decision(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    older = judged(project, [BUG], [BUG])
    edit(project, *CHANGE_RATIO)
    newer = judged(project, [BUG], [BUG])
    [entry] = listing(project, newer)["findings"]
    decide(project, newer, entry["fingerprint"], "reject", *REASON, *REOPEN)
    close(project, newer)

    listed = listing(project, older)
    closed = close(project, older)

    assert listed["decisions_needed"] == []
    assert closed["outcome"] == "done"


# a new contract version


def pattern_draft(project, text: str) -> dict:
    return {
        "name": "code",
        "template": "code",
        "paths": ["src/**"],
        "criteria": [
            {
                "id": "PAT-1",
                "text": text,
                "check": "pattern",
                "required": True,
                "forbid": "TODO",
            }
        ],
    }


def test_a_decision_lapsed_by_a_new_contract_version_is_not_suppressed(project):
    project.ok("contract", "create", stdin=pattern_draft(project, "No TODO"))
    (project.root / "src").mkdir()
    (project.root / "src" / "todo.py").write_text(
        "def first():\n    return 1  # TODO\n", encoding="utf-8"
    )
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [entry] = listing(project, round_id)["findings"]
    decide(project, round_id, entry["fingerprint"], "reject", *REASON, *REOPEN)
    close(project, round_id)
    draft = pattern_draft(project, "No TODO at all")
    preview = project.ok("contract", "change", stdin=draft)
    project.ok("contract", "change", "--confirm", preview["change_hash"], stdin=draft)

    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    listed = listing(project, round_id)
    closed = close(project, round_id)

    assert listed["suppressed"] == []
    [again] = listed["findings"]
    assert again["state"] == "open"
    assert again["presentation"] is None
    assert closed["outcome"] == "continue"
