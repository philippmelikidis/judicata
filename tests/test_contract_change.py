"""contract change: a confirmed change of a stored contract becomes its next version."""

import tomllib

from conftest import command_contract, judgment_contract, proposal, reviewer_output


def created(project, **changes) -> dict:
    return project.ok("contract", "create", stdin=proposal(project, **changes))


def changed_draft(project) -> dict:
    """Version 1 of the code template with every kind of change applied."""
    draft = proposal(project)
    draft["paths"] = ["src/**", "README.md"]
    draft["reviewers"] = {"count": 1, "models": ["inherit"]}
    draft["stop"] = {"max_rounds": 3, "stall_rounds": 2}
    draft["criteria"] = [c for c in draft["criteria"] if c["id"] != "CODE-4"]
    draft["criteria"][2]["text"] = "No known bugs, empty input included"
    draft["criteria"][1]["required"] = False
    draft["criteria"].append(
        {
            "id": "CODE-5",
            "text": "No print statements",
            "check": "pattern",
            "required": True,
            "forbid": r"\bprint\(",
        }
    )
    return draft


def contract_file(project, name: str = "code"):
    return project.root / ".judicata" / "contracts" / f"{name}.toml"


def test_a_proposed_change_shows_the_difference_and_writes_nothing(project):
    created(project)
    before = contract_file(project).read_bytes()
    events = len(project.ledger_events())

    preview = project.ok("contract", "change", stdin=changed_draft(project))

    assert preview["stored"] is False
    assert preview["from_version"] == 1
    assert preview["version"] == 2
    diff = preview["diff"]
    assert diff["paths"] == {
        "from": ["**"],
        "to": ["src/**", "README.md"],
        "added": ["src/**", "README.md"],
        "removed": ["**"],
    }
    assert diff["reviewers"] == {
        "from": {"count": 2, "models": ["inherit"]},
        "to": {"count": 1, "models": ["inherit"]},
    }
    assert diff["stop"] == {
        "from": {"max_rounds": 5, "stall_rounds": 2, "gate": "warn"},
        "to": {"max_rounds": 3, "stall_rounds": 2, "gate": "warn"},
    }
    assert diff["template"] is None
    criteria = diff["criteria"]
    assert [c["id"] for c in criteria["added"]] == ["CODE-5"]
    assert [c["id"] for c in criteria["removed"]] == ["CODE-4"]
    assert {c["id"]: c["changes"] for c in criteria["changed"]} == {
        "CODE-2": [{"field": "required", "from": True, "to": False}],
        "CODE-3": [
            {
                "field": "text",
                "from": "No known correctness bugs in the scope: no input or state "
                "that leads to a wrong result or a crash",
                "to": "No known bugs, empty input included",
            }
        ],
    }
    assert criteria["unchanged"] == ["CODE-1"]
    assert contract_file(project).read_bytes() == before
    assert len(project.ledger_events()) == events


def confirmed(project, draft: dict) -> dict:
    preview = project.ok("contract", "change", stdin=draft)
    return project.ok(
        "contract", "change", "--confirm", preview["change_hash"], stdin=draft
    )


def test_a_confirmed_change_is_stored_as_the_next_version(project):
    created(project)
    draft = changed_draft(project)

    stored = confirmed(project, draft)

    assert stored["stored"] is True
    assert stored["version"] == 2
    on_disk = tomllib.loads(contract_file(project).read_text(encoding="utf-8"))
    assert [c["id"] for c in on_disk["criteria"]] == [
        "CODE-1",
        "CODE-2",
        "CODE-3",
        "CODE-5",
    ]
    assert on_disk["stop"] == {"max_rounds": 3, "stall_rounds": 2, "gate": "warn"}
    shown = project.ok("contract", "show")
    assert (shown["status"], shown["version"]) == ("recorded", 2)
    versions = [
        e["data"] for e in project.ledger_events() if e["type"] == "contract_version"
    ]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["contract_hash"] == stored["contract"]["hash"]
    assert [(c["id"], c["hash"]) for c in versions[1]["criteria"]] == [
        (c["id"], c["hash"]) for c in shown["contract"]["criteria"]
    ]
    assert versions[0]["criteria"][0]["hash"] == versions[1]["criteria"][0]["hash"]
    logged = [
        e["data"] for e in project.ledger_events() if e["type"] == "approval_logged"
    ]
    assert [a["reason"] for a in logged] == ["created", "changed"]


def test_a_confirmation_of_another_difference_stores_nothing(project):
    created(project)
    shown = project.ok("contract", "change", stdin=changed_draft(project))
    other = changed_draft(project)
    other["stop"] = {"max_rounds": 4, "stall_rounds": 2}
    before = contract_file(project).read_bytes()

    error = project.fail(
        "contract", "change", "--confirm", shown["change_hash"], stdin=other
    )

    assert error["code"] == "change_mismatch"
    assert error["details"]["change_hash"] != shown["change_hash"]
    assert contract_file(project).read_bytes() == before
    assert project.ok("contract", "show")["version"] == 1


def test_a_confirmation_after_another_change_was_stored_stores_nothing(project):
    created(project)
    draft = changed_draft(project)
    shown = project.ok("contract", "change", stdin=draft)
    first = proposal(project, stop={"max_rounds": 9, "stall_rounds": 2})
    confirmed(project, first)

    error = project.fail(
        "contract", "change", "--confirm", shown["change_hash"], stdin=draft
    )

    assert error["code"] == "change_mismatch"
    assert project.ok("contract", "show")["contract"]["stop"]["max_rounds"] == 9


def test_a_proposal_equal_to_the_latest_version_is_no_change(project):
    created(project)

    error = project.fail("contract", "change", stdin=proposal(project))

    assert error["code"] == "no_change"
    assert error["details"]["version"] == 1


def test_only_an_existing_contract_can_be_changed(project):
    created(project)

    error = project.fail("contract", "change", stdin=proposal(project, "docs"))

    assert error["code"] == "contract_not_found"
    assert not contract_file(project, "docs").exists()


def test_a_contract_edited_by_hand_is_taken_over_as_the_next_version(project):
    created(project)
    path = contract_file(project)
    path.write_text(
        path.read_text(encoding="utf-8").replace("max_rounds = 5", "max_rounds = 7"),
        encoding="utf-8",
    )

    preview = project.ok("contract", "change", "--from-file")
    stored = project.ok(
        "contract", "change", "--from-file", "--confirm", preview["change_hash"]
    )

    assert preview["file_status"] == "changed"
    assert preview["diff"]["stop"]["to"] == {
        "max_rounds": 7,
        "stall_rounds": 2,
        "gate": "warn",
    }
    assert preview["diff"]["criteria"]["changed"] == []
    assert stored["version"] == 2
    shown = project.ok("contract", "show")
    assert (shown["status"], shown["version"]) == ("recorded", 2)
    assert project.fail("contract", "change", "--from-file")["code"] == "no_change"


def test_a_contract_without_recorded_version_is_recorded_as_version_1(project):
    project.write_contract("code", command_contract("true"))

    preview = project.ok("contract", "change", "--from-file")
    stored = project.ok(
        "contract", "change", "--from-file", "--confirm", preview["change_hash"]
    )

    assert preview["file_status"] == "unrecorded"
    assert preview["from_version"] is None
    assert [c["id"] for c in preview["diff"]["criteria"]["added"]] == ["CODE-1"]
    assert stored["version"] == 1
    assert project.ok("contract", "show")["status"] == "recorded"


def with_command(project, command: str) -> dict:
    draft = proposal(project)
    draft["criteria"][0]["command"] = command
    return draft


def test_changed_commands_are_shown_and_approved_for_the_user_who_confirmed(project):
    created(project)
    draft = with_command(project, "test -d .judicata")

    preview = project.ok("contract", "change", stdin=draft)
    confirmed(project, draft)
    closed = project.review()

    assert preview["approval"]["approved"] is False
    assert preview["approval"]["commands_changed"] is True
    assert preview["approval"]["commands"][0]["command"] == "test -d .judicata"
    assert [c["changes"] for c in preview["diff"]["criteria"]["changed"]] == [
        [{"field": "command", "from": "true", "to": "test -d .judicata"}]
    ]
    assert "CODE-1" in closed["passed"]
    project.state_dir = project.state_dir.parent / "someone-else"
    assert project.ok("contract", "show")["approval"]["approved"] is False


def test_a_change_without_new_commands_keeps_the_approval(project):
    created(project)
    draft = proposal(project, stop={"max_rounds": 3, "stall_rounds": 1})

    preview = project.ok("contract", "change", stdin=draft)

    assert preview["approval"]["approved"] is True
    assert preview["approval"]["commands_changed"] is False


# rounds against the old version


def test_an_open_round_refuses_to_go_on_after_a_change(project):
    created(project)
    round_id = project.ok("round", "start")["round"]
    confirmed(project, proposal(project, stop={"max_rounds": 3, "stall_rounds": 1}))

    for step in (
        ("round", "check", "--round", round_id),
        ("review", "plan", "--round", round_id),
        ("round", "close", "--round", round_id),
    ):
        assert project.fail(*step)["code"] == "contract_changed", step
    assert project.review()["contract"] == "code"


def test_a_round_with_reviewers_refuses_submit_and_close_after_a_change(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    project.ok("contract", "change", "--from-file", "--confirm", project.ok(
        "contract", "change", "--from-file")["change_hash"])  # fmt: skip
    round_id, runs = project.judged_round()
    path = contract_file(project)
    path.write_text(
        path.read_text(encoding="utf-8").replace("max_rounds = 5", "max_rounds = 4"),
        encoding="utf-8",
    )
    project.ok("contract", "change", "--from-file", "--confirm", project.ok(
        "contract", "change", "--from-file")["change_hash"])  # fmt: skip

    code, payload = project.submit(round_id, runs[0], reviewer_output("pass"))

    assert code == 1
    assert payload["error"]["code"] == "contract_changed"
    assert project.fail("round", "close", "--round", round_id)["code"] == (
        "contract_changed"
    )


def test_a_contract_edited_by_hand_cannot_start_a_round_until_recorded(project):
    created(project)
    path = contract_file(project)
    path.write_text(
        path.read_text(encoding="utf-8").replace("max_rounds = 5", "max_rounds = 7"),
        encoding="utf-8",
    )

    error = project.fail("round", "start")

    assert error["code"] == "contract_edited"
    assert error["details"]["latest_version"] == 1
    assert not [e for e in project.ledger_events() if e["type"] == "round_started"]


# decisions across versions

PATTERN = {
    "name": "code",
    "template": "code",
    "paths": ["src/**"],
    "criteria": [
        {
            "id": "PAT-1",
            "text": "No TODO left in the code",
            "check": "pattern",
            "required": True,
            "forbid": "TODO",
        },
        {
            "id": "PAT-2",
            "text": "No FIXME left in the code",
            "check": "pattern",
            "required": True,
            "forbid": "FIXME",
        },
    ],
}
REJECT = ("--decision", "reject", "--reason", "Kept on purpose", "--reopen-if", "never")


def rejected_round(project) -> list[dict]:
    """Version 1 with a TODO and a FIXME, both rejected in a closed round."""
    project.ok("contract", "create", stdin=PATTERN)
    (project.root / "src").mkdir()
    (project.root / "src" / "app.py").write_text(
        "def first():\n    return 1  # TODO\n\n\ndef second():\n    return 2  # FIXME\n",
        encoding="utf-8",
    )
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    entries = project.ok("finding", "list", "--round", round_id)["findings"]
    for entry in entries:
        project.ok(
            "finding", "decide", "--round", round_id,
            "--fingerprint", entry["fingerprint"], *REJECT,
        )  # fmt: skip
    closed = project.ok("round", "close", "--round", round_id)
    assert closed["outcome"] == "required_unmet"  # never quietly done (#33)
    return entries


def by_criterion(project) -> dict:
    found = project.ok("finding", "list", "--contract", "code")["findings"]
    return {f["criterion"]: f for f in found}


def change(project, *criteria) -> dict:
    return confirmed(project, {**PATTERN, "criteria": list(criteria)})


def test_decisions_on_unchanged_criteria_hold_and_on_changed_ones_lapse(project):
    rejected_round(project)
    todo, fixme = PATTERN["criteria"]
    reworded = {**fixme, "text": "No FIXME anywhere"}

    preview = project.ok(
        "contract", "change", stdin={**PATTERN, "criteria": [todo, reworded]}
    )
    change(project, todo, reworded)
    after = by_criterion(project)
    closed = project.review()

    [lapsing] = preview["findings"]["lapse"]
    assert (lapsing["criterion"], lapsing["state"], lapsing["why"]) == (
        "PAT-2",
        "rejected",
        "criterion_changed",
    )
    assert lapsing["decision"]["reason"] == "Kept on purpose"
    assert preview["findings"]["decisions_kept"] == 1
    assert after["PAT-1"]["state"] == "rejected"
    assert after["PAT-2"]["state"] == "closed"
    assert after["PAT-2"]["decision"] is None
    register = project.ok("finding", "list", "--contract", "code")["register"]
    assert [f["criterion"] for f in register] == ["PAT-1"]
    # the changed criterion fails at the same place: open again, to be decided anew
    assert closed["outcome"] == "continue"
    [blocker] = closed["blockers"]
    assert blocker["criterion"] == "PAT-2"
    assert [f["state"] for f in blocker["findings"]] == ["open"]
    assert by_criterion(project)["PAT-1"]["state"] == "rejected"


def test_decisions_on_a_removed_criterion_lapse(project):
    rejected_round(project)
    todo, _ = PATTERN["criteria"]

    change(project, todo)
    after = by_criterion(project)

    assert after["PAT-2"]["state"] == "closed"
    assert after["PAT-2"]["history"][-1]["why"] == "criterion_removed"
    assert after["PAT-1"]["state"] == "rejected"
    assert project.review()["outcome"] == "required_unmet"


def test_a_change_of_paths_or_stop_rule_keeps_every_decision(project):
    rejected_round(project)

    preview = project.ok(
        "contract", "change", stdin={**PATTERN, "stop": {"max_rounds": 3}}
    )
    confirmed(project, {**PATTERN, "stop": {"max_rounds": 3}})

    assert preview["findings"] == {"lapse": [], "decisions_kept": 2}
    assert {f["state"] for f in by_criterion(project).values()} == {"rejected"}
    assert project.review()["outcome"] == "required_unmet"


def test_a_settled_verdict_holds_across_a_change_of_another_criterion(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    project.ok("contract", "change", "--from-file", "--confirm", project.ok(
        "contract", "change", "--from-file")["change_hash"])  # fmt: skip

    def disagreeing_round() -> str:
        round_id, runs = project.judged_round()
        for run, name in zip(runs, ("pass", "fail"), strict=True):
            code, payload = project.submit(round_id, run, reviewer_output(name))
            assert code == 0, payload
        return round_id

    first = disagreeing_round()
    project.ok(
        "review", "decide", "--round", first, "--criterion", "JUD-1",
        "--passed", "true", "--reason", "Callers never pass an empty list",
    )  # fmt: skip
    project.ok("round", "close", "--round", first)
    shown = project.ok("contract", "show")["contract"]
    draft = {k: v for k, v in shown.items() if k != "hash"}
    draft["criteria"] = [
        {k: v for k, v in c.items() if k not in ("hash", "params")} | c["params"]
        for c in shown["criteria"]
    ]
    draft["criteria"][0]["text"] = "The tests pass"
    confirmed(project, draft)

    kept = project.ok("review", "verdicts", "--round", disagreeing_round())

    assert kept["decisions_needed"] == []
    assert kept["criteria"][0]["status"] == "carried_over"

    draft["criteria"][1]["text"] = "No known bugs, empty input included"
    confirmed(project, draft)
    asked = project.ok("review", "verdicts", "--round", disagreeing_round())

    assert asked["decisions_needed"] == ["JUD-1"]


# confirmation and approval edge cases


def test_a_change_without_new_commands_does_not_approve_foreign_commands(project):
    created(project)
    project.state_dir = project.state_dir.parent / "someone-else"
    draft = proposal(project, stop={"max_rounds": 3, "stall_rounds": 1})

    stored = confirmed(project, draft)

    assert stored["approval"]["commands_changed"] is False
    assert stored["approval"]["approved"] is False
    assert project.ok("contract", "show")["approval"]["approved"] is False


def test_a_confirmation_does_not_survive_a_change_and_its_revert(project):
    created(project)
    draft = changed_draft(project)
    shown = project.ok("contract", "change", stdin=draft)
    confirmed(project, proposal(project, stop={"max_rounds": 9, "stall_rounds": 2}))
    confirmed(project, proposal(project))

    error = project.fail(
        "contract", "change", "--confirm", shown["change_hash"], stdin=draft
    )

    assert error["code"] == "change_mismatch"


def test_a_round_stays_stopped_when_the_contract_returns_to_its_version(project):
    created(project)
    round_id = project.ok("round", "start")["round"]
    confirmed(project, proposal(project, stop={"max_rounds": 9, "stall_rounds": 2}))
    confirmed(project, proposal(project))

    error = project.fail("round", "check", "--round", round_id)

    assert error["code"] == "contract_changed"


def test_a_lapsed_verdict_decision_stays_lapsed_when_the_criterion_returns(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    project.ok("contract", "change", "--from-file", "--confirm", project.ok(
        "contract", "change", "--from-file")["change_hash"])  # fmt: skip

    def disagreeing_round() -> str:
        round_id, runs = project.judged_round()
        for run, name in zip(runs, ("pass", "fail"), strict=True):
            code, payload = project.submit(round_id, run, reviewer_output(name))
            assert code == 0, payload
        return round_id

    first = disagreeing_round()
    project.ok(
        "review", "decide", "--round", first, "--criterion", "JUD-1",
        "--passed", "true", "--reason", "Callers never pass an empty list",
    )  # fmt: skip
    project.ok("round", "close", "--round", first)
    path = contract_file(project)
    original = path.read_text(encoding="utf-8")
    for text in (original.replace("correctness bugs", "bugs"), original):
        path.write_text(text, encoding="utf-8")
        preview = project.ok("contract", "change", "--from-file")
        project.ok(
            "contract", "change", "--from-file", "--confirm", preview["change_hash"]
        )

    asked = project.ok("review", "verdicts", "--round", disagreeing_round())

    assert asked["decisions_needed"] == ["JUD-1"]
