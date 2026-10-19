"""Planning the reviewers of a round: count and models from the contract or a per-run override."""

from conftest import command_contract, judgment_contract


def test_contract_defaults_give_two_reviewers_with_the_chat_model(project):
    project.write_contract("code", judgment_contract())

    round_id = project.ok("round", "start")["round"]
    plan = project.ok("review", "plan", "--round", round_id)

    assert [run["model"] for run in plan["runs"]] == ["inherit", "inherit"]
    assert len({run["run"] for run in plan["runs"]}) == 2
    assert plan["criteria"] == [
        {
            "id": "JUD-1",
            "text": "No known correctness bugs in scope",
            "required": True,
            "warnings": [],
        }
    ]
    assert plan["output_schema"]["type"] == "object"
    assert plan["source"] == "contract"


def test_override_changes_count_and_models_for_this_round_only(project):
    path = project.write_contract("code", judgment_contract())
    before = path.read_bytes()

    first = project.ok("round", "start")["round"]
    one = project.ok("review", "plan", "--round", first, "--count", "1")
    second = project.ok("round", "start")["round"]
    named = project.ok("review", "plan", "--round", second, "--models", "opus,sonnet")
    third = project.ok("round", "start")["round"]
    default = project.ok("review", "plan", "--round", third)

    assert [r["model"] for r in one["runs"]] == ["inherit"]
    assert one["source"] == "override"
    assert [r["model"] for r in named["runs"]] == ["opus", "sonnet"]
    assert [r["model"] for r in default["runs"]] == ["inherit", "inherit"]
    assert default["source"] == "contract"
    assert path.read_bytes() == before


def test_models_from_the_contract_are_used_per_reviewer(project):
    project.write_contract(
        "code", judgment_contract(reviewers='count = 2\nmodels = ["opus", "haiku"]\n')
    )
    round_id = project.ok("round", "start")["round"]

    plan = project.ok("review", "plan", "--round", round_id)

    assert [r["model"] for r in plan["runs"]] == ["opus", "haiku"]


def test_without_judgment_criteria_one_reviewer_is_planned(project):
    project.write_contract("code", command_contract("true"))
    round_id = project.ok("round", "start")["round"]

    plan = project.ok("review", "plan", "--round", round_id)

    assert [r["model"] for r in plan["runs"]] == ["inherit"]
    assert plan["criteria"] == []


def test_planning_again_returns_the_same_runs_and_refuses_other_ones(project):
    project.write_contract("code", judgment_contract())
    round_id = project.ok("round", "start")["round"]

    first = project.ok("review", "plan", "--round", round_id)
    again = project.ok("review", "plan", "--round", round_id)
    error = project.fail("review", "plan", "--round", round_id, "--count", "1")

    assert again["runs"] == first["runs"]
    assert error["code"] == "already_planned"


def test_invalid_override_is_a_usage_error(project):
    project.write_contract("code", judgment_contract())
    round_id = project.ok("round", "start")["round"]

    assert (
        project.fail("review", "plan", "--round", round_id, "--count", "0")["code"]
        == "invalid_override"
    )
    assert (
        project.fail("review", "plan", "--round", round_id, "--models", "opus,")["code"]
        == "usage"
    )
