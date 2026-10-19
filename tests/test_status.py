"""judicata status: per contract the outcome, open blockers and should-fix, the
register (accepted risks and rejections with their reasons), the round within
the pass and the open blockers per round. Only from the ledger, writes nothing.
"""

from conftest import proposal
from test_session_hook import context_of
from test_stop_rule import failing, recreate, set_failing


def status(project, *args: str) -> dict:
    return project.ok("status", *args)


def test_a_project_without_judicata_has_no_status_and_gets_nothing_written(project):
    shown = status(project)

    assert shown["contracts"] == []
    assert not (project.root / ".judicata").exists()
    assert not (project.root / ".gitattributes").exists()


def test_a_contract_without_rounds_is_listed_without_an_outcome(project):
    project.write_contract("code", failing(1))

    [entry] = status(project)["contracts"]

    assert entry["contract"] == "code"
    assert entry["outcome"] is None
    assert entry["passes"] == []
    assert entry["blockers"] == []


def test_status_shows_the_round_in_the_pass_and_blockers_per_round(project):
    project.write_contract("code", failing(3))
    for numbers in ((1, 2), (1,)):
        set_failing(project, *numbers)
        project.review()

    [entry] = status(project)["contracts"]

    assert entry["outcome"] == "continue"
    assert entry["outcome_label"] == "continue"
    assert entry["scope"]["kind"] == "contract"
    [current] = entry["passes"]
    assert current["scope"]["kind"] == "contract"
    assert current["pass"] == 1
    assert current["round_in_pass"] == 2
    assert current["ended"] is False
    assert current["next_round"] == 3
    assert current["max_rounds"] == 5
    assert current["stall_rounds"] == 2
    assert current["open_blockers_per_round"] == [2, 1]
    assert [r["outcome"] for r in current["rounds"]] == ["continue", "continue"]
    assert [b["criterion"] for b in entry["blockers"]] == ["CODE-1"]
    assert entry["blockers"][0]["severity"] == "blocker"


def test_after_the_end_of_a_pass_the_next_round_is_round_one(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    for _ in range(3):
        project.review()

    [entry] = status(project)["contracts"]

    assert entry["outcome"] == "not_converged"
    assert entry["outcome_label"] == "not converged"
    [current] = entry["passes"]
    assert current["ended"] is True
    assert current["round_in_pass"] == 3
    assert current["next_round"] == 1
    assert current["open_blockers_per_round"] == [1, 1, 1]


def test_status_lists_should_fix_accepted_risks_and_rejections_with_reasons(project):
    contract = failing(2).replace(
        'id = "CODE-2"\ntext = "Check 2 passes"\ncheck = "command"\nrequired = true',
        'id = "CODE-2"\ntext = "Check 2 passes"\ncheck = "command"\nrequired = false',
    )
    contract += (
        '\n[[criteria]]\nid = "CODE-3"\ntext = "Check 3 passes"\ncheck = "command"\n'
        'required = true\ncommand = "test ! -e fail-3"\n'
    )
    project.write_contract("code", contract)
    set_failing(project, 1, 2)
    first = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", first)
    listed = project.ok("finding", "list", "--round", first)["findings"]
    [code1] = [f for f in listed if f["criterion"] == "CODE-1"]
    project.ok(
        "finding", "decide", "--round", first, "--fingerprint",
        code1["fingerprint"], "--decision", "accept", "--reason",
        "Known flaky test.", "--reopen-if", "CODE-1 changes",
    )  # fmt: skip
    project.ok("round", "close", "--round", first)
    set_failing(project, 1, 2, 3)
    second = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", second)
    listed = project.ok("finding", "list", "--round", second)["findings"]
    [code3] = [f for f in listed if f["criterion"] == "CODE-3"]
    project.ok(
        "finding", "decide", "--round", second, "--fingerprint",
        code3["fingerprint"], "--decision", "reject", "--reason",
        "fail-3 is a test marker.", "--reopen-if", "CODE-3 changes",
    )  # fmt: skip
    [again] = [f for f in listed if f["criterion"] == "CODE-1"]  # files changed
    project.ok(
        "finding", "decide", "--round", second, "--fingerprint",
        again["fingerprint"], "--decision", "keep",
    )  # fmt: skip
    project.ok("round", "close", "--round", second)

    [entry] = status(project)["contracts"]

    # required criteria decided away are never quietly done (#33)
    assert entry["outcome"] == "required_unmet"
    assert entry["outcome_label"] == "required criteria not met"
    assert [u["criterion"] for u in entry["required_unmet"]] == ["CODE-1", "CODE-3"]
    assert entry["finish_confirmed"] is None
    assert entry["blockers"] == []
    assert [f["criterion"] for f in entry["should_fix"]] == ["CODE-2"]
    [accepted] = entry["accepted_risks"]
    assert accepted["criterion"] == "CODE-1"
    assert accepted["reason"] == "Known flaky test."
    assert accepted["reopen_condition"] == "CODE-1 changes"
    [rejected] = entry["rejected"]
    assert rejected["criterion"] == "CODE-3"
    assert rejected["reason"] == "fail-3 is a test marker."
    [current] = entry["passes"]  # unconfirmed unmet criteria count as blockers
    assert (current["pass"], current["round_in_pass"]) == (1, 2)
    assert current["open_blockers_per_round"] == [1, 2]
    assert current["ended"] is False


def test_status_shows_a_confirmed_finish_with_criteria_and_reasons(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [code1] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        code1["fingerprint"], "--decision", "accept", "--reason",
        "Known flaky test.", "--reopen-if", "CODE-1 changes",
    )  # fmt: skip
    closed = project.ok("round", "close", "--round", round_id)
    confirmed = project.ok(
        "round", "finish", "--round", round_id, "--confirm", closed["unmet_hash"]
    )

    [entry] = status(project)["contracts"]

    assert entry["outcome"] == "done_with_unmet_required"
    assert entry["outcome_label"] == "finished despite unmet required criteria"
    assert entry["finish_confirmed"]["event"] == confirmed["event"]
    [unmet] = entry["required_unmet"]
    assert unmet["criterion"] == "CODE-1"
    assert unmet["findings"][0]["decision"]["reason"] == "Known flaky test."
    [current] = entry["passes"]
    assert current["ended"] is True
    assert current["next_round"] == 1


def test_a_focused_round_shows_as_the_last_focus_and_leaves_the_outcome(project):
    project.write_contract("code", failing(2))
    set_failing(project, 1)
    project.review()
    set_failing(project)
    project.review("--criteria", "CODE-1", "--categories", "")

    [entry] = status(project)["contracts"]

    assert entry["outcome"] == "continue"
    assert entry["passes"][0]["round_in_pass"] == 1
    assert entry["last_focus"]["outcome"] == "focus_clear"
    assert entry["last_focus"]["focus"]["criteria"] == ["CODE-1"]


def test_each_scope_has_its_own_pass_and_open_rounds_are_listed(project):
    (project.root / "src").mkdir()
    (project.root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    project.review()
    project.review("--for", "src")
    open_round = project.ok("round", "start")["round"]

    [entry] = status(project)["contracts"]

    assert entry["scope"]["kind"] == "paths"  # the latest counted round
    assert sorted(p["scope"]["kind"] for p in entry["passes"]) == [
        "contract",
        "paths",
    ]
    assert [r["round"] for r in entry["open_rounds"]] == [open_round]


def test_status_of_one_contract_and_an_unknown_one(project):
    project.write_contract("code", failing(1))
    project.write_contract("prose", failing(1).replace('"code"', '"prose"', 2))

    shown = status(project, "--contract", "prose")
    error = project.fail("status", "--contract", "nope")

    assert [c["contract"] for c in shown["contracts"]] == ["prose"]
    assert error["code"] == "contract_not_found"
    assert error["details"]["contracts"] == ["code", "prose"]


def test_a_fixed_blocker_stays_listed_as_resolved_until_a_round_confirms(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "fix",
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)

    [entry] = status(project)["contracts"]

    assert [b["state"] for b in entry["blockers"]] == ["resolved"]


def test_status_says_when_the_contract_changed_since_the_result(project):
    project.write_contract("code", failing(1))
    project.review()

    before = status(project)["contracts"][0]
    project.write_contract("code", failing(2))
    after = status(project)["contracts"][0]

    assert before["outcome"] == "done"
    assert before["contract_changed"] is False
    assert after["outcome"] is None  # done held for the old contract
    assert after["earlier_outcome"]["outcome"] == "done"
    assert after["contract_changed"] is True


def test_findings_of_a_criterion_the_contract_dropped_are_not_blockers(project):
    project.write_contract("code", failing(2))
    set_failing(project, 2)
    project.review()
    project.write_contract("code", failing(1))
    closed = project.review()

    [entry] = status(project)["contracts"]

    assert closed["outcome"] == "done"
    assert entry["outcome"] == "done"
    assert entry["blockers"] == []


def test_an_open_round_shows_the_criteria_that_failed_so_far(project):
    project.write_contract("code", failing(2))
    set_failing(project, 1)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)

    [entry] = status(project)["contracts"]

    [shown] = entry["open_rounds"]
    assert shown["round"] == round_id
    assert shown["failed"] == ["CODE-1"]
    assert shown["focus"] is None


def test_a_result_from_before_a_new_contract_version_is_not_the_current_one(project):
    draft = proposal(project, criteria=proposal(project)["criteria"][:1])
    project.ok("contract", "create", stdin=draft)
    done = project.review()
    recreate(project, draft)

    [entry] = status(project)["contracts"]

    assert done["outcome"] == "done"
    assert entry["contract_changed"] is True
    assert entry["outcome"] is None
    assert entry["earlier_outcome"]["outcome"] == "done"
    assert entry["earlier_outcome"]["round"] == done["round"]
    [shown] = entry["passes"]
    assert shown["superseded"] is True
    assert shown["next_round"] == 1


def test_the_session_hook_names_the_same_result_as_status(project):
    project.write_contract("code", failing(1))
    set_failing(project, 1)
    for _ in range(3):
        project.review()
    project.review("--criteria", "CODE-1", "--categories", "")

    [entry] = status(project)["contracts"]
    context = context_of(project)

    assert entry["outcome_label"] == "not converged"
    assert "last round: not converged, round 3 of at most 5 in pass 1" in context
    assert "open blockers per round 1 -> 1 -> 1" in context
    assert "later a focused round: focus open" in context
