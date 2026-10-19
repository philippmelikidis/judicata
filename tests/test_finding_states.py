"""States of findings over rounds: resolved -> closed or open again, rejected and
accepted hold, criterion findings run through the same states."""

import json

from conftest import command_contract, judgment_contract, reviewer_output

REASON = ("--reason", "Known and harmless here.")
REOPEN = ("--reopen-if", "the function changes")


def finding(**changes) -> dict:
    base = json.loads(reviewer_output("findings"))["findings"][0]
    return {**base, **changes}


def output(*findings, fixture="findings") -> str:
    data = json.loads(reviewer_output(fixture))
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = finding()


def judged(project, *per_run, fixture="findings") -> str:
    """A new round where run i reports the findings in per_run[i]."""
    round_id, runs = project.judged_round()
    for run, found in zip(runs, per_run, strict=True):
        code, payload = project.submit(round_id, run, output(*found, fixture=fixture))
        assert code == 0, payload
    return round_id


def first_round(project, *per_run) -> str:
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    return judged(project, *per_run)


def listed(project, round_id) -> list[dict]:
    return project.ok("finding", "list", "--round", round_id)["findings"]


def decide(project, round_id, fingerprint, decision, *extra) -> dict:
    return project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint", fingerprint,
        "--decision", decision, *extra,
    )  # fmt: skip


def close(project, round_id) -> dict:
    return project.ok("round", "close", "--round", round_id)


def state_of(project, fingerprint, contract="code") -> str:
    found = project.ok("finding", "list", "--contract", contract)["findings"]
    return next(f["state"] for f in found if f["fingerprint"] == fingerprint)


def fixed_in_first_round(project) -> str:
    round_id = first_round(project, [BUG], [BUG])
    [entry] = listed(project, round_id)
    decide(project, round_id, entry["fingerprint"], "fix")
    close(project, round_id)
    return entry["fingerprint"]


def test_a_fixed_finding_not_seen_in_the_next_round_is_closed(project):
    fingerprint = fixed_in_first_round(project)

    closed = close(project, judged(project, [], []))

    assert closed["outcome"] == "done"
    assert state_of(project, fingerprint) == "closed"
    assert closed["transitions"] == [
        {
            "fingerprint": fingerprint,
            "label": "src/shapes.py::ratio",
            "round": closed["round"],
            "from": "resolved",
            "to": "closed",
            "why": "not_seen",
        }
    ]


def test_a_fixed_finding_seen_again_is_open_again_and_blocks(project):
    fingerprint = fixed_in_first_round(project)

    round_id = judged(project, [BUG], [BUG])
    [entry] = listed(project, round_id)
    closed = close(project, round_id)

    assert entry["state"] == "open"
    assert closed["outcome"] == "continue"
    assert state_of(project, fingerprint) == "open"
    assert [(t["from"], t["to"]) for t in closed["transitions"]] == [
        ("resolved", "open")
    ]


def test_a_fixed_finding_stays_resolved_when_no_reviewer_looked(project):
    fingerprint = fixed_in_first_round(project)
    round_id, runs = project.judged_round()
    for run in runs:
        project.ok(
            "review", "discard", "--round", round_id, "--run", run["run"],
            "--reason", "agent failed",
        )  # fmt: skip

    closed = close(project, round_id)

    assert state_of(project, fingerprint) == "resolved"
    assert closed["outcome"] == "continue"
    assert closed["open_blockers"] == 1


def test_an_undecided_note_not_seen_again_is_closed(project):
    round_id = first_round(project, [finding(severity="note")], [])
    [entry] = listed(project, round_id)
    close(project, round_id)

    close(project, judged(project, [], []))

    assert state_of(project, entry["fingerprint"]) == "closed"


def test_a_closed_finding_seen_again_is_open(project):
    fingerprint = fixed_in_first_round(project)
    close(project, judged(project, [], []))

    close(project, judged(project, [BUG], [BUG]))

    assert state_of(project, fingerprint) == "open"


def test_a_rejected_finding_seen_again_stays_rejected_and_does_not_block(project):
    round_id = first_round(project, [BUG], [BUG])
    [entry] = listed(project, round_id)
    decide(project, round_id, entry["fingerprint"], "reject", *REASON, *REOPEN)
    close(project, round_id)

    round_id = judged(project, [BUG], [BUG])
    [again] = project.ok("finding", "list", "--round", round_id)["suppressed"]
    closed = close(project, round_id)

    assert again["state"] == "rejected"
    assert again["decision"]["reason"] == REASON[1]
    assert closed["outcome"] == "done"
    assert state_of(project, entry["fingerprint"]) == "rejected"


def test_the_contract_lists_every_finding_with_its_state_and_the_register(project):
    other = finding(
        file="src/shapes.py",
        line=11,
        quote="return math.pi * self.radius**2",
        symbol="area",
        severity="should-fix",
    )
    round_id = first_round(project, [BUG, other], [BUG, other])
    entries = {e["label"]: e for e in listed(project, round_id)}
    bug, area = entries["src/shapes.py::ratio"], entries["src/shapes.py::Circle.area"]
    decide(project, round_id, bug["fingerprint"], "accept", *REASON, *REOPEN)
    close(project, round_id)

    payload = project.ok("finding", "list", "--contract", "code")

    assert payload["counts"]["accepted"] == 1
    assert payload["counts"]["open"] == 1
    assert [f["fingerprint"] for f in payload["register"]] == [bug["fingerprint"]]
    assert {f["fingerprint"]: f["state"] for f in payload["findings"]} == {
        bug["fingerprint"]: "accepted",
        area["fingerprint"]: "open",
    }


# criterion findings

PATTERN = """name = "code"
template = "code"
paths = ["src/**"]

[[criteria]]
id = "PAT-1"
text = "No TODO left in the code"
check = "pattern"
required = true
forbid = "TODO"
"""

TWO_TODOS = """def first():
    return 1  # TODO


def second():
    return 2  # TODO
"""


def pattern_round(project, text=TWO_TODOS) -> str:
    (project.root / "src").mkdir(exist_ok=True)
    (project.root / "src" / "todo.py").write_text(text, encoding="utf-8")
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    return round_id


def test_a_failed_pattern_criterion_gives_one_finding_per_place(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)

    entries = listed(project, round_id)

    assert [e["label"] for e in entries] == [
        "src/todo.py::first",
        "src/todo.py::second",
    ]
    assert {e["category"] for e in entries} == {"PAT-1"}
    assert {e["source"] for e in entries} == {"criterion"}
    assert {(e["severity"], e["consensus"], e["state"]) for e in entries} == {
        ("blocker", "criterion", "open")
    }
    assert len({e["fingerprint"] for e in entries}) == 2


def test_a_criterion_blocks_until_each_of_its_findings_is_decided(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    first, second = listed(project, round_id)

    decide(project, round_id, first["fingerprint"], "reject", *REASON, *REOPEN)
    partly = listed(project, round_id)
    decide(project, round_id, second["fingerprint"], "accept", *REASON, *REOPEN)
    closed = close(project, round_id)

    assert [e["state"] for e in partly] == ["rejected", "open"]
    # no longer a blocker, but a required criterion is never quietly done (#33)
    assert closed["outcome"] == "required_unmet"
    assert closed["blockers"] == []
    [unmet] = closed["required_unmet"]
    assert unmet["criterion"] == "PAT-1"
    assert [f["state"] for f in unmet["findings"]] == ["rejected", "accepted"]


def test_a_criterion_with_one_open_finding_still_blocks(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    first, _ = listed(project, round_id)

    decide(project, round_id, first["fingerprint"], "reject", *REASON, *REOPEN)
    closed = close(project, round_id)

    assert closed["outcome"] == "continue"
    [blocker] = closed["blockers"]
    assert [f["state"] for f in blocker["findings"]] == ["rejected", "open"]


def test_a_fixed_criterion_finding_closes_when_the_criterion_passes(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    for entry in listed(project, round_id):
        decide(project, round_id, entry["fingerprint"], "fix")
    close(project, round_id)

    closed = close(project, pattern_round(project, "def first():\n    return 1\n"))

    assert closed["outcome"] == "done"
    assert {t["to"] for t in closed["transitions"]} == {"closed"}
    states = project.ok("finding", "list", "--contract", "code")["counts"]
    assert states["closed"] == 2


def test_a_fixed_criterion_finding_still_failing_there_is_open_again(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    first, second = listed(project, round_id)
    decide(project, round_id, first["fingerprint"], "fix")
    decide(project, round_id, second["fingerprint"], "fix")
    close(project, round_id)

    still = "def first():\n    return 1  # TODO\n"
    closed = close(project, pattern_round(project, still))

    assert closed["outcome"] == "continue"
    assert state_of(project, first["fingerprint"]) == "open"
    # the pattern checked every file and second() no longer fails
    assert state_of(project, second["fingerprint"]) == "closed"


def test_a_decision_on_a_criterion_lapses_when_the_criterion_changes(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    for entry in listed(project, round_id):
        decide(project, round_id, entry["fingerprint"], "reject", *REASON, *REOPEN)
    close(project, round_id)

    project.write_contract("code", PATTERN.replace("No TODO left", "No TODO at all"))
    round_id = pattern_round(project)
    entries = listed(project, round_id)

    assert {e["state"] for e in entries} == {"open"}
    assert close(project, round_id)["outcome"] == "continue"


def test_a_decision_on_a_criterion_holds_when_another_criterion_changes(project):
    extra = '\n[[criteria]]\nid = "PAT-2"\ntext = "Short"\ncheck = "pattern"\nrequired = false\nmax_lines = 100\n'
    project.write_contract("code", PATTERN + extra)
    round_id = pattern_round(project)
    for entry in listed(project, round_id):
        decide(project, round_id, entry["fingerprint"], "reject", *REASON, *REOPEN)
    close(project, round_id)

    project.write_contract("code", PATTERN + extra.replace("Short", "Short file"))
    round_id = pattern_round(project)
    entries = project.ok("finding", "list", "--round", round_id)["suppressed"]

    assert {e["state"] for e in entries} == {"rejected"}
    assert close(project, round_id)["outcome"] == "required_unmet"


def test_a_failed_command_criterion_is_one_finding_for_the_whole_scope(project):
    project.write_contract("code", command_contract("test -f ok"))
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)

    [entry] = listed(project, round_id)
    decide(project, round_id, entry["fingerprint"], "fix")
    first = close(project, round_id)
    (project.root / "ok").write_text("", encoding="utf-8")
    second = project.review()

    assert entry["address"] == {"kind": "scope"}
    assert entry["category"] == "CODE-1"
    assert first["outcome"] == "continue"
    assert second["outcome"] == "done"
    assert state_of(project, entry["fingerprint"]) == "closed"


def test_an_accepted_failing_command_is_required_unmet_not_done(project):
    project.write_contract("code", command_contract("false"))
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [entry] = listed(project, round_id)

    decide(project, round_id, entry["fingerprint"], "accept", *REASON, *REOPEN)
    closed = close(project, round_id)

    assert closed["outcome"] == "required_unmet"
    assert closed["required_unmet"][0]["criterion"] == "CODE-1"


def test_a_failed_judgment_gives_a_finding_at_the_cited_place(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = judged(project, [], [], fixture="fail")

    [entry] = listed(project, round_id)
    decide(project, round_id, entry["fingerprint"], "fix")
    first = close(project, round_id)
    second = close(project, judged(project, [], [], fixture="pass"))

    assert entry["label"] == "src/app.py::average"
    assert entry["category"] == "JUD-1"
    assert entry["lines"] == [6]
    assert first["outcome"] == "continue"
    assert second["outcome"] == "done"
    assert state_of(project, entry["fingerprint"]) == "closed"


# order of rounds and decisions


def test_a_round_started_before_the_fix_does_not_close_it(project):
    round_a = first_round(project, [], [])
    round_b = judged(project, [BUG], [BUG])
    [entry] = listed(project, round_b)
    decide(project, round_b, entry["fingerprint"], "fix")
    close(project, round_b)

    close(project, round_a)

    assert state_of(project, entry["fingerprint"]) == "resolved"


def test_a_round_started_before_the_fix_does_not_reopen_it(project):
    round_a = first_round(project, [BUG], [BUG])
    round_b = judged(project, [BUG], [BUG])
    [entry] = listed(project, round_b)
    decide(project, round_b, entry["fingerprint"], "fix")
    close(project, round_b)

    close(project, round_a)

    assert state_of(project, entry["fingerprint"]) == "resolved"


def test_an_undecided_confirmed_blocker_stays_open_and_blocks(project):
    round_id = first_round(project, [BUG], [BUG])
    [entry] = listed(project, round_id)
    close(project, round_id)

    round_id = judged(project, [], [])
    payload = project.ok("finding", "list", "--round", round_id)
    closed = close(project, round_id)

    assert [e["fingerprint"] for e in payload["earlier"]] == [entry["fingerprint"]]
    assert payload["earlier"][0]["last_round"] != round_id
    assert state_of(project, entry["fingerprint"]) == "open"
    assert closed["outcome"] == "continue"


def test_a_finding_is_decided_in_the_latest_round_that_saw_it(project):
    round_a = first_round(project, [BUG], [BUG])
    close(project, round_a)
    round_b = judged(project, [BUG], [BUG])
    close(project, round_b)
    [entry] = listed(project, round_b)

    error = project.fail(
        "finding", "decide", "--round", round_a, "--fingerprint",
        entry["fingerprint"], "--decision", "accept", *REASON, *REOPEN,
    )  # fmt: skip

    assert error["code"] == "stale_round"
    assert error["details"]["latest_round"] == round_b


def test_a_fixed_place_of_a_still_failing_pattern_is_closed(project):
    project.write_contract("code", PATTERN)
    round_id = pattern_round(project)
    first, second = listed(project, round_id)
    decide(project, round_id, first["fingerprint"], "fix")
    decide(project, round_id, second["fingerprint"], "accept", *REASON, *REOPEN)
    close(project, round_id)

    only_second = (
        "def first():\n    return 1\n\n\ndef second():\n    return 2  # TODO\n"
    )
    closed = close(project, pattern_round(project, only_second))

    assert state_of(project, first["fingerprint"]) == "closed"
    assert state_of(project, second["fingerprint"]) == "accepted"
    assert closed["outcome"] == "required_unmet"


# narrowed and focused rounds only judge what they looked at


def narrowed(project, *start_args, per_run=([], [])) -> str:
    round_id = project.ok("round", "start", *start_args)["round"]
    project.ok("round", "check", "--round", round_id)
    runs = project.ok("review", "plan", "--round", round_id)["runs"]
    for run, found in zip(runs, per_run, strict=False):
        code, payload = project.submit(round_id, run, output(*found))
        assert code == 0, payload
    return round_id


def test_a_round_narrowed_to_other_files_does_not_close_a_fixed_finding(project):
    fingerprint = fixed_in_first_round(project)  # in src/shapes.py

    close(project, narrowed(project, "--for", "src/app.py"))

    assert state_of(project, fingerprint) == "resolved"


def test_a_round_focused_on_other_categories_does_not_close_a_fixed_finding(project):
    fingerprint = fixed_in_first_round(project)  # correctness

    closed = close(project, narrowed(project, "--categories", "security"))

    assert closed["focused"] is True
    assert state_of(project, fingerprint) == "resolved"


def test_a_focused_round_is_clear_despite_open_blockers_outside_its_focus(project):
    round_id = first_round(project, [BUG], [BUG])
    close(project, round_id)  # a confirmed correctness blocker stays open

    closed = close(project, narrowed(project, "--categories", "security"))

    assert closed["outcome"] == "focus_clear"


def test_a_narrowed_round_does_not_close_criterion_places_outside_its_files(project):
    project.write_contract("code", PATTERN)
    (project.root / "src").mkdir()
    (project.root / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")
    round_id = pattern_round(project)
    for entry in listed(project, round_id):
        decide(project, round_id, entry["fingerprint"], "fix")
    close(project, round_id)

    round_id = project.ok("round", "start", "--for", "src/other.py")["round"]
    project.ok("round", "check", "--round", round_id)
    closed = close(project, round_id)

    assert closed["outcome"] == "done"
    counts = project.ok("finding", "list", "--contract", "code")["counts"]
    assert counts["resolved"] == 2
