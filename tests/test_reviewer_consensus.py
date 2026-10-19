"""Judgment criteria: reviewers that agree settle the result, disagreements go to the user once."""

from conftest import judgment_contract, reviewer_output


def submit_all(project, round_id, runs, *names):
    for run, name in zip(runs, names, strict=True):
        code, payload = project.submit(round_id, run, reviewer_output(name))
        assert code == 0, payload


def test_two_reviewers_that_agree_on_pass_make_the_round_done(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    submit_all(project, round_id, runs, "pass", "pass")
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "done"
    assert closed["passed"] == ["CODE-1", "JUD-1"]
    assert closed["pending"] == []


def test_two_reviewers_that_agree_on_fail_give_a_blocker_with_their_evidence(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    submit_all(project, round_id, runs, "fail", "fail")
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "continue"
    [blocker] = closed["blockers"]
    assert blocker["criterion"] == "JUD-1"
    assert blocker["severity"] == "blocker"
    assert blocker["evidence"]["consensus"] == "agreed"
    [first, second] = blocker["evidence"]["verdicts"]
    assert first["evidence"] == [
        {"file": "src/app.py", "line": 6, "quote": "return sum(values) / len(values)"}
    ]
    assert {first["run"], second["run"]} == {r["run"] for r in runs}


def test_every_sighting_records_run_model_and_round(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round("--models", "opus,sonnet")

    submit_all(project, round_id, runs, "pass", "fail")

    sightings = [
        e["data"] for e in project.ledger_events() if e["type"] == "reviewer_output"
    ]
    assert [(s["round"], s["run"], s["model"]) for s in sightings] == [
        (round_id, runs[0]["run"], "opus"),
        (round_id, runs[1]["run"], "sonnet"),
    ]


def test_optional_judgment_criterion_that_fails_is_should_fix(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract(required=False))
    round_id, runs = project.judged_round()

    submit_all(project, round_id, runs, "fail", "fail")
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "done"
    assert [s["criterion"] for s in closed["should_fix"]] == ["JUD-1"]


def test_a_single_reviewer_decides_directly(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round("--count", "1")

    submit_all(project, round_id, runs, "fail")
    closed = project.ok("round", "close", "--round", round_id)

    [blocker] = closed["blockers"]
    assert blocker["evidence"]["consensus"] == "single"


def disagree(project, *plan_args):
    round_id, runs = project.judged_round(*plan_args)
    submit_all(project, round_id, runs, "pass", "fail")
    return round_id


def test_disagreement_is_put_to_the_user_and_blocks_closing_until_decided(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = disagree(project)

    verdicts = project.ok("review", "verdicts", "--round", round_id)
    error = project.fail("round", "close", "--round", round_id)

    assert verdicts["complete"] is True
    assert verdicts["decisions_needed"] == ["JUD-1"]
    [entry] = verdicts["criteria"]
    assert entry["status"] == "disagreed"
    assert sorted(v["passed"] for v in entry["verdicts"]) == [False, True]
    assert error["code"] == "decision_needed"
    assert error["details"]["criteria"] == ["JUD-1"]


def test_the_users_decision_settles_the_criterion_and_is_stored(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = disagree(project)

    project.ok(
        "review", "decide", "--round", round_id, "--criterion", "JUD-1",
        "--passed", "true", "--reason", "Callers never pass an empty list",
    )  # fmt: skip
    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "done"
    assert "JUD-1" in closed["passed"]
    [decision] = [e for e in project.ledger_events() if e["type"] == "verdict_decided"]
    assert decision["data"]["criterion"] == "JUD-1"
    assert decision["data"]["passed"] is True
    assert decision["data"]["reason"] == "Callers never pass an empty list"


def test_agreed_criteria_cannot_be_overruled_by_a_decision(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    submit_all(project, round_id, runs, "fail", "fail")

    error = project.fail(
        "review", "decide", "--round", round_id, "--criterion", "JUD-1",
        "--passed", "true", "--reason", "I disagree",
    )  # fmt: skip

    assert error["code"] == "nothing_to_decide"


def decide_pass(project, round_id):
    project.ok(
        "review", "decide", "--round", round_id, "--criterion", "JUD-1",
        "--passed", "true", "--reason", "Callers never pass an empty list",
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)


def test_decision_holds_in_later_rounds_while_criterion_and_files_are_unchanged(
    project,
):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    decide_pass(project, disagree(project))

    later = disagree(project)
    verdicts = project.ok("review", "verdicts", "--round", later)
    closed = project.ok("round", "close", "--round", later)

    assert verdicts["decisions_needed"] == []
    [entry] = verdicts["criteria"]
    assert entry["status"] == "carried_over"
    assert entry["decision"]["reason"] == "Callers never pass an empty list"
    assert closed["outcome"] == "done"


def test_decision_is_asked_again_when_a_cited_file_changed(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    decide_pass(project, disagree(project))

    app = project.root / "src" / "app.py"
    app.write_text(app.read_text() + "\n\ndef median(values):\n    return values[0]\n")
    later = disagree(project)

    assert project.ok("review", "verdicts", "--round", later)["decisions_needed"] == [
        "JUD-1"
    ]


def test_decision_is_asked_again_when_the_criterion_changed(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    decide_pass(project, disagree(project))

    project.write_contract(
        "code",
        judgment_contract().replace(
            "No known correctness bugs in scope", "No known bugs, empty input included"
        ),
    )
    later = disagree(project)

    assert project.ok("review", "verdicts", "--round", later)["decisions_needed"] == [
        "JUD-1"
    ]


def test_one_valid_run_out_of_two_is_unconfirmed_and_goes_to_the_user(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    submit_all(project, round_id, runs[:1], "fail")
    project.ok(
        "review", "discard", "--round", round_id, "--run", runs[1]["run"],
        "--reason", "agent returned no output",
    )  # fmt: skip

    verdicts = project.ok("review", "verdicts", "--round", round_id)

    [entry] = verdicts["criteria"]
    assert entry["status"] == "unconfirmed"
    assert verdicts["decisions_needed"] == ["JUD-1"]
    assert verdicts["discarded"] == [
        {
            "run": runs[1]["run"],
            "model": "inherit",
            "reason": "agent returned no output",
        }
    ]


def test_closing_waits_for_every_planned_run(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    submit_all(project, round_id, runs[:1], "pass")

    error = project.fail("round", "close", "--round", round_id)

    assert error["code"] == "reviews_incomplete"
    assert error["details"]["waiting"] == [runs[1]["run"]]


def test_decision_is_asked_again_when_a_file_it_covered_changed(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    submit_all(project, round_id, runs, "pass", "fail_two_files")
    decide_pass(project, round_id)

    stats = project.root / "src" / "stats.py"
    stats.write_text(stats.read_text().replace("max(values)", "min(values)"))
    later = disagree(project)  # cites only src/app.py, which is unchanged

    assert project.ok("review", "verdicts", "--round", later)["decisions_needed"] == [
        "JUD-1"
    ]


def test_decision_holds_even_when_both_reviewers_later_agree_otherwise(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    decide_pass(project, disagree(project))

    later, runs = project.judged_round()
    submit_all(project, later, runs, "fail", "fail")
    verdicts = project.ok("review", "verdicts", "--round", later)
    closed = project.ok("round", "close", "--round", later)

    [entry] = verdicts["criteria"]
    assert entry["status"] == "carried_over"
    assert entry["passed"] is True
    assert closed["outcome"] == "done"


def test_agreement_citing_a_place_the_decision_did_not_cover_stands(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    decide_pass(project, disagree(project))  # covered src/app.py only

    later, runs = project.judged_round()
    submit_all(project, later, runs, "fail_two_files", "fail_two_files")
    closed = project.ok("round", "close", "--round", later)

    [blocker] = closed["blockers"]
    assert blocker["evidence"]["consensus"] == "agreed"
