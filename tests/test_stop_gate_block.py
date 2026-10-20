"""Stop gate, block mode (#15).

With `gate = "block"` in a contract's [stop] table, or JUDICATA_GATE=block in
the environment of the hook, `judicata.py hook stop` answers a problem of the
warn mode (see test_stop_gate.py) with

  {"decision": "block", "reason": "...", "systemMessage": "judicata: ..."}

The reason tells the agent what is open (blockers, changed files) and what to
do: review again, or tell the user honestly that the work is not done and end
with the line "judicata: not done" (last_assistant_message), which ends the
turn without a block. Final results release the gate as in warn mode. A session gets at most three blocks;
then the gate lets go, warns and records a gate_released event in the ledger.
JUDICATA_GATE overrides the contract in both directions.
"""

import json
import time

import pytest
from test_stop_gate import (
    _done,
    _finished_despite_unmet,
    _not_converged,
    _round_limit,
    edit,
    failing,
    review,
    session,
    setup,
)


def stop(project, hook_input) -> dict | None:
    """The Stop hook's answer as a dict, or None when it prints nothing."""
    code, stdout, stderr = project.hook("stop", stdin=hook_input)
    assert code == 0, stderr
    if stdout == "":
        return None
    payload = json.loads(stdout)
    for key in ("reason", "systemMessage"):
        if key in payload:
            assert "\n" not in payload[key]
    if "systemMessage" in payload:
        assert payload["systemMessage"].startswith("judicata")
    return payload


def blocked(payload) -> bool:
    return payload is not None and payload.get("decision") == "block"


def released(project) -> list[dict]:
    return [e for e in project.ledger_events() if e["type"] == "gate_released"]


def open_review(project, **kwargs) -> dict:
    """A contract with an open blocker from this session's review."""
    setup(project, **kwargs)
    failing(project, kwargs.get("name", "code"))
    return session(project, review(project, "--contract", kwargs.get("name", "code")))


# block


def test_block_mode_blocks_with_the_open_blockers_and_what_to_do(project):
    hook_input = open_review(project, gate="block")

    payload = stop(project, hook_input)

    assert blocked(payload)
    reason = payload["reason"]
    assert reason.startswith("judicata")
    assert "code" in reason and "CODE-1" in reason and "1 open blocker" in reason
    assert "review" in reason.lower()
    assert "not done" in reason
    assert "1 of 3" in reason
    # the user sees it too, in one line
    assert "CODE-1" in payload["systemMessage"]


def test_block_mode_names_the_changed_files(project):
    source = setup(project, gate="block")
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source)

    payload = stop(project, hook_input)

    assert blocked(payload)
    assert "src/code/app.py" in payload["reason"]
    assert "written in this session" in payload["reason"]


def test_a_review_that_was_not_closed_blocks(project):
    setup(project, gate="block")
    round_id = project.ok("round", "start")["round"]

    payload = stop(project, session(project, round_id))

    assert blocked(payload)
    assert "not closed" in payload["reason"]


@pytest.mark.parametrize(
    ("limits", "play"),
    [
        ({}, _done),
        ({}, _finished_despite_unmet),
        ({"max_rounds": 1}, _round_limit),
        ({"stall_rounds": 1}, _not_converged),
    ],
)
def test_a_final_result_releases_the_gate(project, limits, play):
    setup(project, gate="block", **limits)
    round_ids = play(project)

    assert stop(project, session(project, *round_ids)) is None


def test_a_review_after_the_block_releases_the_gate(project):
    hook_input = open_review(project, gate="block")
    assert blocked(stop(project, hook_input))

    failing(project, on=False)
    again = review(project)
    hook_input = session(project, again)

    assert stop(project, hook_input) is None


def test_a_session_that_touched_no_contract_is_never_blocked(project):
    setup(project, gate="block")
    failing(project)
    review(project)  # open blockers, but not from this session

    assert stop(project, session(project)) is None


def test_a_codex_session_is_blocked_the_same_way(project):
    setup(project, gate="block")
    failing(project)
    round_id = review(project)

    assert blocked(stop(project, session(project, round_id, codex=True)))


# an honest report


@pytest.mark.parametrize(
    "last",
    [
        "Not finished: CODE-1 still fails.\n\njudicata: not done",
        "Noch offen ist CODE-1.\n\n**judicata: Not done**",
        "judicata: not done (CODE-1 is open)",
    ],
)
def test_an_honest_not_done_ends_the_turn_without_a_block(project, last):
    hook_input = open_review(project, gate="block")

    payload = stop(project, {**hook_input, "last_assistant_message": last})

    assert set(payload) == {"systemMessage"}
    # it did not count as a block
    assert "1 of 3" in stop(project, hook_input)["reason"]


@pytest.mark.parametrize(
    "last",
    [
        "All done.",
        "The work is not done yet, but I think it is fine.",
        "I could say judicata: not done, but it is done.",
        None,
        7,
    ],
)
def test_anything_else_is_no_honest_report(project, last):
    hook_input = open_review(project, gate="block")

    payload = stop(project, {**hook_input, "last_assistant_message": last})

    assert blocked(payload)
    assert "judicata: not done" in payload["reason"]


# the limit


def test_after_three_blocks_the_gate_lets_go_and_records_it(project):
    hook_input = open_review(project, gate="block")
    reasons = []
    for _ in range(3):
        payload = stop(project, hook_input)
        assert blocked(payload)
        reasons.append(payload["reason"])
        hook_input = {**hook_input, "stop_hook_active": True}
    assert "1 of 3" in reasons[0] and "3 of 3" in reasons[2]
    assert released(project) == []

    payload = stop(project, hook_input)

    assert not blocked(payload)
    assert set(payload) == {"systemMessage"}
    message = payload["systemMessage"]
    assert "3 blocks" in message and "CODE-1" in message
    [event] = released(project)
    assert event["data"]["session"] == "s1"
    assert event["data"]["blocks"] == 3
    assert event["data"]["contracts"] == ["code"]
    assert event["data"]["reason"] == "block_limit"

    # later stops of the same session only warn, and record nothing more
    payload = stop(project, {**hook_input, "stop_hook_active": False})
    assert set(payload) == {"systemMessage"}
    assert len(released(project)) == 1
    assert project.ok("status")["contracts"][0]["outcome"] == "continue"


def test_every_session_has_its_own_three_blocks(project):
    hook_input = open_review(project, gate="block")
    for _ in range(3):
        assert blocked(stop(project, hook_input))
    assert not blocked(stop(project, hook_input))

    payload = stop(project, {**hook_input, "session_id": "s2"})

    assert blocked(payload) and "1 of 3" in payload["reason"]


def test_blocks_in_warn_mode_do_not_count(project):
    hook_input = open_review(project)  # warn
    for _ in range(4):
        assert set(stop(project, hook_input)) == {"systemMessage"}

    project.env["JUDICATA_GATE"] = "block"

    assert "1 of 3" in stop(project, hook_input)["reason"]


def test_without_a_place_to_count_it_blocks_only_once_per_turn(project):
    hook_input = open_review(project, gate="block")
    not_a_dir = project.root.parent / "state-file"
    not_a_dir.write_text("x\n", encoding="utf-8")
    project.env["JUDICATA_STATE_DIR"] = str(not_a_dir)

    assert blocked(stop(project, hook_input))
    again = stop(project, {**hook_input, "stop_hook_active": True})

    assert not blocked(again) and "systemMessage" in again


def test_without_a_session_id_it_blocks_only_once_per_turn(project):
    hook_input = open_review(project, gate="block")
    del hook_input["session_id"]

    assert blocked(stop(project, hook_input))
    assert not blocked(stop(project, {**hook_input, "stop_hook_active": True}))


# the environment


def test_the_environment_switches_a_warn_contract_to_block(project):
    hook_input = open_review(project)
    project.env["JUDICATA_GATE"] = "block"

    assert blocked(stop(project, hook_input))


def test_the_environment_switches_a_block_contract_to_warn(project):
    hook_input = open_review(project, gate="block")
    project.env["JUDICATA_GATE"] = "warn"

    payload = stop(project, hook_input)

    assert set(payload) == {"systemMessage"}


@pytest.mark.parametrize("value", ["", "off", "blocking"])
def test_an_unknown_environment_value_keeps_the_contract(project, value):
    hook_input = open_review(project, gate="block")
    project.env["JUDICATA_GATE"] = value

    assert blocked(stop(project, hook_input))


def test_the_environment_value_ignores_case_and_spaces(project):
    hook_input = open_review(project)
    project.env["JUDICATA_GATE"] = " Block "

    assert blocked(stop(project, hook_input))


def test_without_judicata_the_environment_changes_nothing(project):
    project.env["JUDICATA_GATE"] = "block"
    hook_input = session(project, "0" * 32)

    assert stop(project, hook_input) is None


# several contracts


def test_only_block_contracts_block_and_the_others_still_warn(project):
    setup(project, name="code", gate="block")
    setup(project, name="docs")
    failing(project, "code")
    failing(project, "docs")
    code_round = review(project, "--contract", "code")
    docs_round = review(project, "--contract", "docs")

    payload = stop(project, session(project, code_round, docs_round))

    assert blocked(payload)
    assert "code" in payload["reason"]
    assert "docs" not in payload["reason"]
    assert "code" in payload["systemMessage"] and "docs" in payload["systemMessage"]


def test_warn_contracts_alone_never_block(project):
    setup(project, name="code")
    setup(project, name="docs")
    failing(project, "code")
    failing(project, "docs")
    rounds = [review(project, "--contract", n) for n in ("code", "docs")]

    assert set(stop(project, session(project, *rounds))) == {"systemMessage"}
