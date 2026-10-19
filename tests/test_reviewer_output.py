"""Reviewer output is checked against the fixed schema; invalid output gets one retry."""

import json

import pytest
from conftest import judgment_contract, reviewer_output


def fail_output(**changes) -> dict:
    output = json.loads(reviewer_output("fail"))
    output["criteria"][0].update(changes)
    return output


def evidence(**changes) -> list[dict]:
    place = {
        "file": "src/app.py",
        "line": 6,
        "quote": "return sum(values) / len(values)",
    }
    return [{**place, **changes}]


INVALID = {
    "not JSON": ("average is broken", "(root)", "not valid JSON"),
    "empty": ("", "(root)", "empty"),
    "unknown top-level field": (
        {**fail_output(), "summary": "one bug"},
        "summary",
        "unknown field",
    ),
    "passed is not a boolean": (fail_output(passed="no"), "passed", "boolean"),
    "reason missing": (
        {"criteria": [{"id": "JUD-1", "passed": True, "evidence": []}]},
        "reason",
        "required",
    ),
    "no verdict for the criterion": (
        {"criteria": []},
        "criteria",
        "no verdict for JUD-1",
    ),
    "unknown criterion id": (fail_output(id="JUD-9"), "id", "not a judgment criterion"),
    "verdict for a command criterion": (
        fail_output(id="CODE-1"),
        "id",
        "not a judgment criterion",
    ),
    "failed without evidence": (fail_output(evidence=[]), "evidence", "at least one"),
    "file does not exist": (
        fail_output(evidence=evidence(file="src/missing.py")),
        "file",
        "no such file",
    ),
    "file outside the project": (
        fail_output(evidence=evidence(file="../outside.py")),
        "file",
        "not a path inside the project",
    ),
    "file name with a null byte": (
        fail_output(evidence=evidence(file="src/app\u0000.py")),
        "file",
        "not a path inside the project",
    ),
    "line past the end": (fail_output(evidence=evidence(line=99)), "line", "only 6"),
    "quote not in the file": (
        fail_output(evidence=evidence(quote="return statistics.mean(values)")),
        "quote",
        "not found",
    ),
}


@pytest.mark.parametrize("case", list(INVALID))
def test_invalid_output_is_rejected_with_the_problem(project, case):
    output, field, fragment = INVALID[case]
    text = output if isinstance(output, str) else json.dumps(output)
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    code, payload = project.submit(round_id, runs[0], text)

    assert code != 0
    error = payload["error"]
    assert error["code"] == "invalid_reviewer_output"
    assert error["details"]["retry"] is True
    assert any(
        p["field"].endswith(field) and fragment in p["problem"]
        for p in error["details"]["problems"]
    ), error["details"]["problems"]
    types = [e["type"] for e in project.ledger_events()]
    assert "reviewer_output" not in types


def test_a_retry_with_valid_output_is_accepted(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    first, _ = project.submit(round_id, runs[0], "not json")
    second, payload = project.submit(round_id, runs[0], reviewer_output("fail"))

    assert first != 0
    assert second == 0, payload
    assert payload["waiting"] == [runs[1]["run"]]


def test_second_invalid_output_discards_the_run_and_the_report_names_it(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    project.submit(round_id, runs[0], "not json")

    code, payload = project.submit(round_id, runs[0], "still not json")
    late, late_payload = project.submit(round_id, runs[0], reviewer_output("fail"))
    project.submit(round_id, runs[1], reviewer_output("fail"))
    project.ok(
        "review", "decide", "--round", round_id, "--criterion", "JUD-1",
        "--passed", "false", "--reason", "The second reviewer is right",
    )  # fmt: skip
    closed = project.ok("round", "close", "--round", round_id)

    assert code != 0
    assert payload["error"]["details"]["retry"] is False
    assert payload["error"]["details"]["discarded"] is True
    assert late != 0
    assert late_payload["error"]["code"] == "run_discarded"
    assert closed["reviewers"]["discarded"] == [
        {"run": runs[0]["run"], "model": "inherit", "reason": "invalid output twice"}
    ]
    assert [b["criterion"] for b in closed["blockers"]] == ["JUD-1"]


def test_when_every_run_is_discarded_the_criterion_stays_pending(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round("--count", "1")
    project.submit(round_id, runs[0], "no")
    project.submit(round_id, runs[0], "no")

    closed = project.ok("round", "close", "--round", round_id)

    assert closed["outcome"] == "continue"
    assert closed["pending"][0]["criterion"] == "JUD-1"
    assert closed["pending"][0]["reason"] == "no valid reviewer output"


def test_output_wrapped_in_a_code_fence_is_accepted(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    code, payload = project.submit(
        round_id, runs[0], "```json\n" + reviewer_output("pass") + "\n```\n"
    )

    assert code == 0, payload


def test_a_run_takes_only_one_accepted_output(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    project.submit(round_id, runs[0], reviewer_output("pass"))

    code, payload = project.submit(round_id, runs[0], reviewer_output("fail"))

    assert code != 0
    assert payload["error"]["code"] == "run_already_submitted"


def test_unknown_run_and_unplanned_round_are_reported(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id = project.ok("round", "start")["round"]

    _, payload = project.submit(round_id, {"run": "nope"}, reviewer_output("pass"))
    assert payload["error"]["code"] == "not_planned"

    project.ok("review", "plan", "--round", round_id)
    _, payload = project.submit(round_id, {"run": "nope"}, reviewer_output("pass"))
    assert payload["error"]["code"] == "run_not_found"


def test_output_can_be_read_from_a_file(project, tmp_path):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    path = tmp_path / "out.json"
    path.write_text(reviewer_output("pass"), encoding="utf-8")

    payload = project.ok(
        "review", "submit", "--round", round_id, "--run", runs[0]["run"],
        "--file", str(path),
    )  # fmt: skip

    assert payload["verdicts"] == 1


def test_deeply_nested_json_is_invalid_output_not_a_crash(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()

    code, payload = project.submit(round_id, runs[0], "[" * 100000 + "]" * 100000)

    assert code != 0
    assert payload["error"]["code"] == "invalid_reviewer_output"


def scoped_round(project):
    """A round whose contract covers src/** only; docs/notes.md lies outside."""
    project.add_artifact()
    notes = project.root / "docs" / "notes.md"
    notes.parent.mkdir(exist_ok=True)
    notes.write_text("average() must return 0 for an empty list.\n", encoding="utf-8")
    project.write_contract(
        "code", judgment_contract().replace('paths = ["**"]', 'paths = ["src/**"]')
    )
    return project.judged_round()


def test_a_failed_verdict_needs_a_place_inside_the_contract_scope(project):
    round_id, runs = scoped_round(project)
    outside = evidence(
        file="docs/notes.md", line=1, quote="average() must return 0 for an empty list."
    )

    code, payload = project.submit(
        round_id, runs[0], json.dumps(fail_output(evidence=outside))
    )
    accepted, _ = project.submit(
        round_id, runs[1], json.dumps(fail_output(evidence=evidence() + outside))
    )

    assert code != 0
    assert any(
        "scope" in p["problem"] for p in payload["error"]["details"]["problems"]
    ), payload
    assert accepted == 0


def test_a_passed_verdict_may_cite_files_outside_the_scope(project):
    round_id, runs = scoped_round(project)
    outside = evidence(
        file="docs/notes.md", line=1, quote="average() must return 0 for an empty list."
    )
    output = json.loads(reviewer_output("pass"))
    output["criteria"][0]["evidence"] = outside

    code, payload = project.submit(round_id, runs[0], json.dumps(output))

    assert code == 0, payload


def test_judicata_state_is_never_evidence(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    place = evidence(
        file=".judicata/contracts/code.toml", line=1, quote='name = "code"'
    )

    code, payload = project.submit(
        round_id, runs[0], json.dumps(fail_output(evidence=place))
    )

    assert code != 0
    assert payload["error"]["code"] == "invalid_reviewer_output"


def test_plan_names_the_contract_paths_for_the_reviewers(project):
    project.write_contract(
        "code", judgment_contract().replace('paths = ["**"]', 'paths = ["src/**"]')
    )
    round_id = project.ok("round", "start")["round"]

    assert project.ok("review", "plan", "--round", round_id)["paths"] == ["src/**"]
