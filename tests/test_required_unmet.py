"""A failed required criterion is never quietly done (#33).

Rejecting or accepting its findings stays possible, but round close then says
required_unmet with the criteria and the user's decisions. Only round finish
with the confirmation hash records finish_confirmed and makes the round
done_with_unmet_required. The confirmation holds for exactly those criteria,
places and decisions.
"""

import json

import pytest
from conftest import command_contract, judgment_contract, reviewer_output

REASON = "Flaky on this machine only."
DECIDE = ("--reason", REASON, "--reopen-if", "the test runs in CI")

TWO_REQUIRED = command_contract("false") + (
    "\n[[criteria]]\n"
    'id = "CODE-2"\n'
    'text = "Lint is clean"\n'
    'check = "command"\n'
    "required = true\n"
    'command = "test ! -f lint.err"\n'
)


def started(project) -> str:
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    return round_id


def entries(project, round_id) -> list[dict]:
    return project.ok("finding", "list", "--round", round_id)["findings"]


def decide(project, round_id, fingerprint, decision, *extra) -> dict:
    return project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint", fingerprint,
        "--decision", decision, *extra,
    )  # fmt: skip


def close(project, round_id) -> dict:
    return project.ok("round", "close", "--round", round_id)


def finish(project, round_id, confirm) -> dict:
    return project.ok("round", "finish", "--round", round_id, "--confirm", confirm)


def decided_round(project, decision="accept", *, contract=None) -> dict:
    """A round whose failing command criterion the user decided; closed."""
    (project.root / "app.txt").write_text("v1\n", encoding="utf-8")
    project.write_contract("code", contract or command_contract("false"))
    round_id = started(project)
    for entry in entries(project, round_id):
        decide(project, round_id, entry["fingerprint"], decision, *DECIDE)
    return close(project, round_id)


def events(project, kind) -> list[dict]:
    return [e for e in project.ledger_events() if e["type"] == kind]


def context(project) -> str:
    code, stdout, _ = project.hook()
    assert code == 0
    return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    ("decision", "state"), [("accept", "accepted"), ("reject", "rejected")]
)
def test_a_decided_required_criterion_is_required_unmet_not_done(
    project, decision, state
):
    closed = decided_round(project, decision)

    assert closed["outcome"] == "required_unmet"
    assert closed["blockers"] == []
    assert closed["decided"] == []
    [unmet] = closed["required_unmet"]
    assert unmet["criterion"] == "CODE-1"
    assert unmet["text"] == "Tests pass"
    [place] = unmet["findings"]
    assert place["state"] == state
    assert place["decision"]["decision"] == decision
    assert place["decision"]["reason"] == REASON
    assert closed["unmet_hash"]
    [event] = events(project, "round_closed")
    assert event["data"]["outcome"] == "required_unmet"
    assert event["data"]["unmet_hash"] == closed["unmet_hash"]
    assert not events(project, "finish_confirmed")


def test_a_decided_optional_criterion_stays_done_with_accepted_risks(project):
    closed = decided_round(project, contract=command_contract("false", required=False))

    assert closed["outcome"] == "done"
    assert closed["required_unmet"] == []
    assert closed["unmet_hash"] is None
    assert [d["criterion"] for d in closed["decided"]] == ["CODE-1"]


def test_an_open_required_criterion_next_to_a_decided_one_is_continue(project):
    (project.root / "lint.err").write_text("E1\n", encoding="utf-8")
    (project.root / "app.txt").write_text("v1\n", encoding="utf-8")
    project.write_contract("code", TWO_REQUIRED)
    round_id = started(project)
    first = next(e for e in entries(project, round_id) if e["criterion"] == "CODE-1")
    decide(project, round_id, first["fingerprint"], "accept", *DECIDE)

    closed = close(project, round_id)

    assert closed["outcome"] == "continue"
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-2"]
    assert [u["criterion"] for u in closed["required_unmet"]] == ["CODE-1"]


def test_confirming_records_the_finish_and_names_criteria_and_decisions(project):
    closed = decided_round(project)

    done = finish(project, closed["round"], closed["unmet_hash"])

    assert done["outcome"] == "done_with_unmet_required"
    assert done["written"] is True
    assert done["label"] == "finished despite unmet required criteria"
    [unmet] = done["required_unmet"]
    assert unmet["criterion"] == "CODE-1"
    [event] = events(project, "finish_confirmed")
    data = event["data"]
    assert data["round"] == closed["round"]
    assert data["contract"] == "code"
    assert data["unmet_hash"] == closed["unmet_hash"]
    [stored] = data["required_unmet"]
    assert stored["criterion"] == "CODE-1"
    [place] = stored["findings"]
    assert place["decision"]["decision"] == "accept"
    assert place["decision"]["reason"] == REASON


def test_confirming_twice_writes_once(project):
    closed = decided_round(project)
    finish(project, closed["round"], closed["unmet_hash"])

    again = finish(project, closed["round"], closed["unmet_hash"])

    assert again["outcome"] == "done_with_unmet_required"
    assert again["written"] is False
    assert len(events(project, "finish_confirmed")) == 1


def test_a_wrong_confirmation_is_refused_and_writes_nothing(project):
    closed = decided_round(project)

    error = project.fail(
        "round", "finish", "--round", closed["round"], "--confirm", "0" * 64
    )

    assert error["code"] == "confirm_mismatch"
    assert error["details"]["unmet_hash"] == closed["unmet_hash"]
    assert not events(project, "finish_confirmed")


@pytest.mark.parametrize(
    "contract",
    [command_contract("true"), command_contract("false", required=False)],
    ids=["passed", "optional"],
)
def test_only_a_required_unmet_round_can_be_confirmed(project, contract):
    (project.root / "app.txt").write_text("v1\n", encoding="utf-8")
    project.write_contract("code", contract)
    round_id = started(project)
    for entry in entries(project, round_id):
        decide(project, round_id, entry["fingerprint"], "accept", *DECIDE)
    closed = close(project, round_id)

    error = project.fail("round", "finish", "--round", round_id, "--confirm", "x")

    assert closed["outcome"] == "done"
    assert error["code"] == "nothing_to_confirm"


def test_an_open_round_cannot_be_confirmed(project):
    (project.root / "app.txt").write_text("v1\n", encoding="utf-8")
    project.write_contract("code", command_contract("false"))
    round_id = started(project)

    error = project.fail("round", "finish", "--round", round_id, "--confirm", "x")

    assert error["code"] == "round_open"


def test_a_round_followed_by_another_cannot_be_confirmed(project):
    closed = decided_round(project)
    later = started(project)

    error = project.fail(
        "round", "finish", "--round", closed["round"], "--confirm", closed["unmet_hash"]
    )

    assert error["code"] == "stale_round"
    assert error["details"]["latest_round"] == later


FAILING = {
    "name": "code",
    "template": "code",
    "paths": ["**"],
    "criteria": [
        {
            "id": "CODE-1",
            "text": "Tests pass",
            "check": "command",
            "required": True,
            "command": "false",
        }
    ],
}


CHANGES = {
    "reworded": [{**FAILING["criteria"][0], "text": "All pass"}],
    "added": [
        FAILING["criteria"][0],
        {
            "id": "CODE-2",
            "text": "Lint is clean",
            "check": "command",
            "required": True,
            "command": "true",
        },
    ],
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_confirmation_after_a_contract_change_is_outdated(project, change):
    (project.root / "app.txt").write_text("v1\n", encoding="utf-8")
    project.ok("contract", "create", stdin=FAILING)
    round_id = started(project)
    [entry] = entries(project, round_id)
    decide(project, round_id, entry["fingerprint"], "accept", *DECIDE)
    closed = close(project, round_id)
    changed = {**FAILING, "criteria": CHANGES[change]}
    preview = project.ok("contract", "change", stdin=changed)
    project.ok("contract", "change", "--confirm", preview["change_hash"], stdin=changed)

    error = project.fail(
        "round", "finish", "--round", round_id, "--confirm", closed["unmet_hash"]
    )

    assert closed["outcome"] == "required_unmet"
    assert error["code"] == "finish_outdated"
    assert not events(project, "finish_confirmed")


def test_a_file_changed_after_close_makes_the_confirmation_outdated(project):
    closed = decided_round(project)
    (project.root / "app.txt").write_text("v2\n", encoding="utf-8")

    error = project.fail(
        "round", "finish", "--round", closed["round"], "--confirm", closed["unmet_hash"]
    )

    assert error["code"] == "finish_outdated"


def test_a_blocker_found_by_a_later_focused_round_prevents_the_finish(project):
    project.add_artifact()
    project.write_contract(
        "code", judgment_contract().replace('command = "true"', 'command = "false"')
    )
    first, runs = project.judged_round()
    for run in runs:
        project.submit(first, run, reviewer_output("pass"))
    [entry] = entries(project, first)
    decide(project, first, entry["fingerprint"], "accept", *DECIDE)
    closed = close(project, first)
    focused = project.ok("round", "start", "--categories", "correctness")["round"]
    project.ok("round", "check", "--round", focused)
    bug = json.loads(reviewer_output("findings"))
    for run in project.ok("review", "plan", "--round", focused)["runs"]:
        code, payload = project.submit(focused, run, json.dumps(bug))
        assert code == 0, payload
    assert close(project, focused)["blocking_findings"]

    error = project.fail(
        "round", "finish", "--round", first, "--confirm", closed["unmet_hash"]
    )

    assert closed["outcome"] == "required_unmet"
    assert error["code"] == "finish_outdated"


def test_without_confirmation_the_round_stays_open_in_the_hook(project):
    decided_round(project)

    text = context(project)

    assert "last round: required criteria not met (CODE-1 fail" in text
    assert "finished despite unmet required criteria" not in text


def test_the_hook_shows_a_confirmed_finish_with_criteria_and_reasons(project):
    closed = decided_round(project)
    finish(project, closed["round"], closed["unmet_hash"])

    text = context(project)

    assert "finished despite unmet required criteria" in text
    line = next(x for x in text.splitlines() if x.startswith("- CODE-1"))
    assert "Tests pass" in line
    assert "accepted" in line
    assert REASON in line


def test_an_unchanged_next_round_carries_the_confirmation(project):
    closed = decided_round(project)
    confirmed = finish(project, closed["round"], closed["unmet_hash"])

    again = close(project, started(project))

    assert again["outcome"] == "done_with_unmet_required"
    assert again["unmet_hash"] == closed["unmet_hash"]
    assert again["confirmation"]["event"] == confirmed["event"]
    assert len(events(project, "finish_confirmed")) == 1


def test_a_changed_place_asks_again(project):
    closed = decided_round(project)
    finish(project, closed["round"], closed["unmet_hash"])
    (project.root / "app.txt").write_text("v2\n", encoding="utf-8")
    round_id = started(project)
    [entry] = entries(project, round_id)
    decide(project, round_id, entry["fingerprint"], "keep")

    again = close(project, round_id)

    assert entry["presentation"] == "represented"
    assert again["outcome"] == "required_unmet"
    assert again["confirmation"] is None
    assert again["unmet_hash"] != closed["unmet_hash"]


def test_a_new_unmet_required_criterion_asks_again(project):
    closed = decided_round(project, contract=TWO_REQUIRED)
    finish(project, closed["round"], closed["unmet_hash"])
    (project.root / "lint.err").write_text("E1\n", encoding="utf-8")
    round_id = started(project)
    for entry in entries(project, round_id):
        if entry["criterion"] == "CODE-2":
            decide(project, round_id, entry["fingerprint"], "accept", *DECIDE)
        else:  # the new file changed the scope place of CODE-1 too
            decide(project, round_id, entry["fingerprint"], "keep")

    again = close(project, round_id)

    assert again["outcome"] == "required_unmet"
    assert [u["criterion"] for u in again["required_unmet"]] == ["CODE-1", "CODE-2"]


def test_a_rejected_required_judgment_is_required_unmet(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run in runs:
        code, payload = project.submit(round_id, run, reviewer_output("fail"))
        assert code == 0, payload
    for entry in entries(project, round_id):
        if entry["source"] == "criterion":
            decide(project, round_id, entry["fingerprint"], "reject", *DECIDE)

    closed = close(project, round_id)

    assert closed["outcome"] == "required_unmet"
    assert [u["criterion"] for u in closed["required_unmet"]] == ["JUD-1"]


def test_a_focused_round_with_an_unmet_required_criterion_is_focus_open(project):
    closed = decided_round(project, contract=TWO_REQUIRED)
    finish(project, closed["round"], closed["unmet_hash"])
    round_id = project.ok("round", "start", "--criteria", "CODE-1")["round"]
    project.ok("round", "check", "--round", round_id)

    focused = close(project, round_id)

    assert focused["outcome"] == "focus_open"
    assert [u["criterion"] for u in focused["required_unmet"]] == ["CODE-1"]
    error = project.fail("round", "finish", "--round", round_id, "--confirm", "x")
    assert error["code"] == "nothing_to_confirm"
