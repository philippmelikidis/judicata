"""Decisions on findings: fix, reject or accept risk, recorded in the ledger."""

import json

import pytest
from conftest import judgment_contract, reviewer_output


def finding(**changes) -> dict:
    base = json.loads(reviewer_output("findings"))["findings"][0]
    return {**base, **changes}


def output(*findings) -> str:
    data = json.loads(reviewer_output("findings"))
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = finding()


def reviewed(project, *per_run) -> str:
    """A round where run i reports the findings in per_run[i]; returns the round id."""
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run, found in zip(runs, per_run, strict=True):
        code, payload = project.submit(round_id, run, output(*found))
        assert code == 0, payload
    return round_id


def listed(project, round_id) -> list[dict]:
    return project.ok("finding", "list", "--round", round_id)["findings"]


def decide(project, round_id, fingerprint, decision, *extra) -> dict:
    return project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint", fingerprint,
        "--decision", decision, *extra,
    )  # fmt: skip


def test_fix_needs_no_reason_and_marks_the_finding_resolved(project):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    decided = decide(project, round_id, entry["fingerprint"], "fix")
    [after] = listed(project, round_id)

    assert entry["state"] == "open"
    assert decided["state"] == "resolved"
    assert after["state"] == "resolved"
    assert after["decision"]["decision"] == "fix"
    [event] = [e for e in project.ledger_events() if e["type"] == "finding_decided"]
    assert event["data"]["round"] == round_id
    assert event["data"]["fingerprint"] == entry["fingerprint"]
    assert event["data"]["decision"] == "fix"
    assert event["ts"]


REASON = ("--reason", "Callers never pass zero.")
REOPEN = ("--reopen-if", "ratio gets a caller that passes user input")


@pytest.mark.parametrize("decision", ["reject", "accept"])
def test_reject_and_accept_need_a_reason(project, decision):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", decision, "--reason", "  ", *REOPEN,
    )  # fmt: skip

    assert error["code"] == "reason_required"
    assert not [e for e in project.ledger_events() if e["type"] == "finding_decided"]


@pytest.mark.parametrize("decision", ["reject", "accept"])
def test_reject_and_accept_need_a_reopen_condition(project, decision):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", decision, *REASON,
    )  # fmt: skip

    assert error["code"] == "reopen_condition_required"


@pytest.mark.parametrize(
    ("decision", "state"), [("reject", "rejected"), ("accept", "accepted")]
)
def test_reject_and_accept_store_reason_and_reopen_condition_and_unblock(
    project, decision, state
):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], decision, *REASON, *REOPEN)
    closed = project.ok("round", "close", "--round", round_id)
    register = project.ok("finding", "list", "--contract", "code")["register"]

    assert closed["outcome"] == "done"
    assert closed["open_blockers"] == 0
    assert closed["findings"][0]["state"] == state
    [kept] = register
    assert kept["state"] == state
    assert kept["decision"]["reason"] == "Callers never pass zero."
    assert kept["decision"]["reopen_condition"] == REOPEN[1]
    assert kept["decision"]["section_hash"] == entry["section_hash"]
    assert kept["decision"]["ts"]
    [event] = [e for e in project.ledger_events() if e["type"] == "finding_decided"]
    assert event["data"]["reason"] == "Callers never pass zero."
    assert event["data"]["reopen_condition"] == REOPEN[1]


def test_a_fixed_blocker_still_blocks_the_round_it_was_found_in(project):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], "fix")
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "continue"
    assert closed["open_blockers"] == 1
    assert closed["findings"][0]["state"] == "resolved"


def test_an_unconfirmed_candidate_must_be_decided_before_close(project):
    round_id = reviewed(project, [BUG], [])
    [entry] = listed(project, round_id)

    error = project.fail("round", "close", "--round", round_id)

    assert entry["consensus"] == "unconfirmed"
    assert entry["needs_decision"] is True
    assert error["code"] == "decision_needed"
    assert error["details"]["findings"] == [entry["fingerprint"]]


@pytest.mark.parametrize(
    ("decision", "extra", "outcome", "state"),
    [
        ("fix", (), "continue", "resolved"),
        ("accept", REASON + REOPEN, "done", "accepted"),
        ("reject", REASON + REOPEN, "done", "rejected"),
    ],
)
def test_a_decision_confirms_or_rejects_an_unconfirmed_candidate(
    project, decision, extra, outcome, state
):
    round_id = reviewed(project, [BUG], [])
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], decision, *extra)
    [after] = listed(project, round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert after["needs_decision"] is False
    assert after["confirmed"] is (decision != "reject")
    assert closed["outcome"] == outcome
    assert closed["findings"][0]["state"] == state


def test_a_decision_within_the_open_round_can_be_replaced(project):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], "fix")
    decide(project, round_id, entry["fingerprint"], "accept", *REASON, *REOPEN)
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["findings"][0]["state"] == "accepted"
    assert closed["outcome"] == "done"


def test_a_decided_finding_of_a_closed_round_takes_no_new_decision(project):
    round_id = reviewed(project, [BUG], [BUG])
    [entry] = listed(project, round_id)
    decide(project, round_id, entry["fingerprint"], "reject", *REASON, *REOPEN)
    project.ok("round", "close", "--round", round_id)

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "fix",
    )  # fmt: skip

    assert error["code"] == "finding_not_open"
    assert error["details"]["state"] == "rejected"


def test_an_open_finding_of_a_closed_round_can_still_be_decided(project):
    round_id = reviewed(project, [BUG], [BUG])
    project.ok("round", "close", "--round", round_id)
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], "fix")
    [after] = listed(project, round_id)

    assert entry["state"] == "open"
    assert after["state"] == "resolved"


def test_an_unknown_fingerprint_is_refused(project):
    round_id = reviewed(project, [BUG], [BUG])

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint", "f" * 64,
        "--decision", "fix",
    )  # fmt: skip

    assert error["code"] == "finding_not_found"


def test_decisions_wait_until_every_run_is_in(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    project.submit(round_id, runs[0], output(BUG))

    error = project.fail(
        "finding", "decide", "--round", round_id, "--fingerprint", "f" * 64,
        "--decision", "fix",
    )  # fmt: skip

    assert error["code"] == "reviews_incomplete"
