"""Stop rule: after each round the core reports done, continue, not converged or
round limit reached, with the limits from the contract.

Rounds count per contract and scope within a pass; a pass ends with done, not
converged or round limit, and the next review starts a new pass at round 1. A
focused round reports for its focus only and does not count.
"""

import json

from conftest import command_contract, judgment_contract, proposal, reviewer_output


def failing(count: int, *, max_rounds: int = 5, stall_rounds: int = 2) -> str:
    """A contract with `count` required command criteria; CODE-n fails while
    the file fail-n exists in the project."""
    lines = [
        'name = "code"',
        'template = "code"',
        'paths = ["**"]',
        "",
        "[stop]",
        f"max_rounds = {max_rounds}",
        f"stall_rounds = {stall_rounds}",
    ]
    for n in range(1, count + 1):
        lines += [
            "",
            "[[criteria]]",
            f'id = "CODE-{n}"',
            f'text = "Check {n} passes"',
            'check = "command"',
            "required = true",
            f'command = "test ! -e fail-{n}"',
        ]
    return "\n".join(lines) + "\n"


def set_failing(project, *numbers: int, total: int = 5) -> None:
    """Exactly the criteria with these numbers fail in the next round."""
    for n in range(1, total + 1):
        marker = project.root / f"fail-{n}"
        if n in numbers:
            marker.write_text("x\n", encoding="utf-8")
        elif marker.exists():
            marker.unlink()


def outcomes(project, *rounds_failing: tuple[int, ...]) -> list[dict]:
    """One review per entry; each entry names the criteria that fail in it."""
    closed = []
    for numbers in rounds_failing:
        set_failing(project, *numbers)
        closed.append(project.review())
    return closed


def test_a_clean_artifact_is_done_in_round_one_and_ends_the_pass(project):
    project.write_contract("code", command_contract("true"))

    closed = project.review()

    assert closed["outcome"] == "done"
    assert closed["outcome_label"] == "done"
    assert closed["stop"]["counted"] is True
    assert closed["stop"]["round_in_pass"] == 1
    assert closed["stop"]["pass"] == 1
    assert closed["stop"]["ended"] is True
    assert closed["stop"]["open_blockers_per_round"] == [0]


def test_two_rounds_without_fewer_blockers_after_the_first_are_not_converged(project):
    project.write_contract("code", failing(2))

    first, second, third = outcomes(project, (1,), (1,), (1,))

    assert first["outcome"] == "continue"  # the first round is the starting value
    assert first["stop"]["stalled_rounds"] == 0
    assert first["stop"]["ended"] is False
    assert second["outcome"] == "continue"  # one round without progress
    assert second["stop"]["stalled_rounds"] == 1
    assert third["outcome"] == "not_converged"
    assert third["outcome_label"] == "not converged"
    assert third["stop"]["round_in_pass"] == 3
    assert third["stop"]["stalled_rounds"] == 2
    assert third["stop"]["open_blockers_per_round"] == [1, 1, 1]
    assert third["stop"]["ended"] is True
    [*_, last] = [e for e in project.ledger_events() if e["type"] == "round_closed"]
    assert last["data"]["outcome"] == "not_converged"
    assert last["data"]["round_in_pass"] == 3


def test_fewer_blockers_keep_the_pass_going_until_they_stop_going_down(project):
    project.write_contract("code", failing(3, max_rounds=6))

    closed = outcomes(project, (1, 2, 3), (1, 2), (1,), (1,), (1,))

    assert [c["outcome"] for c in closed] == [
        "continue",
        "continue",
        "continue",
        "continue",  # round 3 went down, round 4 is the first without progress
        "not_converged",
    ]
    assert [c["stop"]["stalled_rounds"] for c in closed] == [0, 0, 0, 1, 2]
    assert closed[-1]["stop"]["open_blockers_per_round"] == [3, 2, 1, 1, 1]


def test_more_blockers_than_before_count_as_no_progress(project):
    project.write_contract("code", failing(2))

    closed = outcomes(project, (1,), (1, 2), (1, 2))

    assert [c["outcome"] for c in closed] == ["continue", "continue", "not_converged"]
    assert closed[-1]["stop"]["open_blockers_per_round"] == [1, 2, 2]


def test_the_round_limit_ends_the_pass(project):
    project.write_contract("code", failing(5, max_rounds=3, stall_rounds=5))

    closed = outcomes(project, (1, 2, 3, 4, 5), (1, 2, 3, 4), (1, 2, 3))

    assert [c["outcome"] for c in closed] == ["continue", "continue", "round_limit"]
    assert closed[-1]["outcome_label"] == "round limit reached"
    assert closed[-1]["stop"]["round_in_pass"] == 3
    assert closed[-1]["stop"]["max_rounds"] == 3
    assert closed[-1]["stop"]["ended"] is True


def test_done_in_the_last_round_is_done(project):
    project.write_contract("code", failing(2, max_rounds=2))

    closed = outcomes(project, (1, 2), ())

    assert [c["outcome"] for c in closed] == ["continue", "done"]


def test_the_limits_come_from_the_contract(project):
    project.write_contract("code", failing(1, max_rounds=1, stall_rounds=3))
    set_failing(project, 1)
    limited = project.review()

    project.write_contract("code", failing(1, max_rounds=5, stall_rounds=1))
    start = project.review()
    stalled = project.review()

    assert limited["outcome"] == "round_limit"
    assert start["stop"]["round_in_pass"] == 1  # a new pass after the limit
    assert start["outcome"] == "continue"
    assert stalled["outcome"] == "not_converged"  # one round without progress
    assert stalled["stop"]["stall_rounds"] == 1
    assert stalled["stop"]["round_in_pass"] == 2


def test_after_an_end_the_next_review_starts_a_new_pass_at_round_one(project):
    project.write_contract("code", failing(1))

    closed = outcomes(project, (1,), (1,), (1,), (1,), (), (1,))

    assert [c["outcome"] for c in closed] == [
        "continue",
        "continue",
        "not_converged",
        "continue",
        "done",
        "continue",
    ]
    assert [(c["stop"]["pass"], c["stop"]["round_in_pass"]) for c in closed] == [
        (1, 1),
        (1, 2),
        (1, 3),
        (2, 1),
        (2, 2),
        (3, 1),
    ]
    assert closed[3]["stop"]["open_blockers_per_round"] == [1]


def test_a_focused_round_never_reports_done_and_does_not_count(project):
    project.write_contract("code", failing(2))
    set_failing(project, 1)
    first = project.review()
    set_failing(project)
    focus_clear = project.review("--criteria", "CODE-1,CODE-2", "--categories", "")
    set_failing(project, 1)
    focus_open = project.review("--criteria", "CODE-1", "--categories", "")
    project.review()
    second = project.review()

    assert first["outcome"] == "continue"
    assert focus_clear["outcome"] == "focus_clear"
    assert focus_clear["outcome_label"] == "focus clear"
    assert focus_clear["stop"]["counted"] is False
    assert focus_open["outcome"] == "focus_open"
    assert focus_open["stop"]["counted"] is False
    assert second["outcome"] == "not_converged"  # the focused rounds did not count
    assert second["stop"]["round_in_pass"] == 3
    assert second["stop"]["open_blockers_per_round"] == [1, 1, 1]


def test_a_clear_focus_does_not_end_the_pass(project):
    project.write_contract("code", failing(2))
    set_failing(project, 1, 2)
    project.review()
    set_failing(project)
    focused = project.review("--criteria", "CODE-1", "--categories", "")
    done = project.review()

    assert focused["outcome"] == "focus_clear"
    assert done["outcome"] == "done"
    assert done["stop"]["round_in_pass"] == 2


def test_rounds_count_per_scope(project):
    (project.root / "src").mkdir()
    (project.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    project.write_contract("code", failing(1))
    set_failing(project, 1)

    whole = project.review()
    project.review()
    narrowed = project.review("--for", "src")
    again = project.review()

    assert whole["stop"]["round_in_pass"] == 1
    assert narrowed["stop"]["round_in_pass"] == 1
    assert narrowed["outcome"] == "continue"
    assert again["stop"]["round_in_pass"] == 3
    assert again["outcome"] == "not_converged"


def decide_all(project, round_id: str, decision: str) -> None:
    listed = project.ok("finding", "list", "--round", round_id)["findings"]
    assert listed
    for entry in listed:
        project.ok(
            "finding", "decide", "--round", round_id, "--fingerprint",
            entry["fingerprint"], "--decision", decision, "--reason",
            "The check is flaky on this machine.", "--reopen-if", "CODE-1 changes",
        )  # fmt: skip


def test_done_with_accepted_risks_is_never_a_silent_done(project):
    project.write_contract(
        "code", failing(1).replace("required = true", "required = false")
    )
    set_failing(project, 1)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    decide_all(project, round_id, "accept")

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "done"
    assert closed["outcome_label"] == "done with accepted risks"
    [risk] = closed["accepted_risks"]
    assert risk["kind"] == "criterion"
    assert risk["criterion"] == "CODE-1"
    assert risk["severity"] == "should-fix"
    [entry] = risk["findings"]
    assert entry["state"] == "accepted"
    assert entry["decision"]["reason"] == "The check is flaky on this machine."
    [ended] = [e for e in project.ledger_events() if e["type"] == "round_closed"]
    assert ended["data"]["accepted_risks"] == 1


def accepted_round(project) -> dict:
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    for entry in project.ok("finding", "list", "--round", round_id)["findings"]:
        if entry["state"] == "open":
            decide_all(project, round_id, "accept")
            break
    return project.ok("round", "close", "--round", round_id)


def test_an_unconfirmed_unmet_required_criterion_counts_as_a_blocker(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)

    rounds_closed = [accepted_round(project) for _ in range(3)]

    assert [r["outcome"] for r in rounds_closed] == [
        "required_unmet",
        "required_unmet",
        "not_converged",
    ]
    assert rounds_closed[0]["outcome_label"] == "required criteria not met"
    assert rounds_closed[0]["accepted_risks"] == []
    assert rounds_closed[0]["stop"]["ended"] is False
    assert rounds_closed[-1]["stop"]["open_blockers_per_round"] == [1, 1, 1]


def test_a_confirmed_finish_ends_the_pass(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    first = accepted_round(project)
    project.ok(
        "round", "finish", "--round", first["round"], "--confirm", first["unmet_hash"]
    )

    again = accepted_round(project)

    assert again["outcome"] == "done_with_unmet_required"
    assert again["outcome_label"] == "finished despite unmet required criteria"
    # the confirmation ended pass 1; the carried one ends pass 2 at once
    assert (again["stop"]["pass"], again["stop"]["round_in_pass"]) == (2, 1)
    assert again["stop"]["ended"] is True


def test_a_plain_done_has_no_accepted_risks(project):
    project.write_contract("code", failing(1))

    closed = project.review()

    assert closed["outcome_label"] == "done"
    assert closed["accepted_risks"] == []


def test_a_rejected_reviewer_blocker_is_listed_with_its_reason(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    bug = json.loads(reviewer_output("findings"))
    round_id, runs = project.judged_round()
    for run in runs:
        code, payload = project.submit(round_id, run, json.dumps(bug))
        assert code == 0, payload
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "reject", "--reason",
        "Callers never pass zero.", "--reopen-if", "ratio() changes",
    )  # fmt: skip

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "done"
    assert closed["outcome_label"] == "done with accepted risks"
    [risk] = closed["accepted_risks"]
    assert risk["kind"] == "finding"
    assert risk["state"] == "rejected"
    assert risk["severity"] == "blocker"
    assert risk["reason"] == "Callers never pass zero."
    assert risk["reopen_condition"] == "ratio() changes"


def test_the_same_paths_in_another_order_are_the_same_scope(project):
    for folder in ("src", "tests", "src/sub"):
        (project.root / folder).mkdir(parents=True, exist_ok=True)
        (project.root / folder / "a.py").write_text("x = 1\n", encoding="utf-8")
    project.write_contract("code", failing(1))
    set_failing(project, 1)

    first = project.review("--for", "src", "--for", "tests")
    project.review("--for", "tests", "--for", "src")
    third = project.review("--for", "tests", "--for", "src", "--for", "src/sub")

    assert first["outcome"] == "continue"
    assert third["stop"]["round_in_pass"] == 3
    assert third["outcome"] == "not_converged"


def test_a_changed_contract_starts_a_new_pass(project):
    project.write_contract("code", failing(2))
    set_failing(project, 1)
    project.review()
    first = project.review()
    project.write_contract("code", failing(2).replace("Check 2", "Check two"))
    second = project.review()

    assert first["outcome"] == "continue"
    assert second["outcome"] == "continue"  # not "not converged": a new pass
    assert (second["stop"]["pass"], second["stop"]["round_in_pass"]) == (2, 1)


def recreate(project, draft: dict) -> None:
    """Store the contract again as a new version (same content)."""
    (project.root / ".judicata" / "contracts" / "code.toml").unlink()
    project.ok("contract", "create", stdin=draft)


def test_a_new_contract_version_starts_a_new_pass(project):
    draft = proposal(project)
    project.ok("contract", "create", stdin=draft)
    project.review()
    first = project.review()  # judgment criteria stay pending: not done
    recreate(project, draft)
    second = project.review()

    assert first["outcome"] == "continue"
    assert second["outcome"] == "continue"  # not "not converged": a new pass
    assert (second["stop"]["pass"], second["stop"]["round_in_pass"]) == (2, 1)
