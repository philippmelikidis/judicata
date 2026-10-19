"""Invalid contracts are rejected with a clear message before anything runs."""

import pytest
from conftest import command_contract

VALID = command_contract("true")


def problems(error: dict) -> list[str]:
    assert error["code"] == "invalid_contract", error
    return [f"{p['field']}: {p['problem']}" for p in error["details"]["problems"]]


def assert_rejected(project, text: str, *expected: str) -> dict:
    project.write_contract("code", text)
    error = project.fail("round", "start")
    found = problems(error)
    for fragment in expected:
        assert any(fragment in p for p in found), (fragment, found)
        assert fragment in error["message"]
    assert not project.ledger.exists(), "nothing may be written for an invalid contract"
    return error


@pytest.mark.parametrize("field", ["name", "template", "paths", "criteria"])
def test_missing_top_level_field_is_rejected(project, field):
    text = "\n".join(
        line for line in VALID.splitlines() if not line.startswith(f"{field} ")
    )
    if field == "criteria":
        text = VALID.split("[[criteria]]")[0]
    assert_rejected(project, text, f"{field}: required")


@pytest.mark.parametrize("field", ["id", "text", "check", "required"])
def test_missing_criterion_field_is_rejected(project, field):
    text = "\n".join(
        line for line in VALID.splitlines() if not line.startswith(f"{field} ")
    )
    assert_rejected(project, text, f".{field}: ")


def test_missing_command_is_rejected(project):
    text = "\n".join(
        line for line in VALID.splitlines() if not line.startswith("command ")
    )
    assert_rejected(project, text, "command: required")


def test_duplicate_criterion_ids_are_rejected(project):
    second = VALID.split("\n\n", 1)[1]
    assert_rejected(project, VALID + "\n" + second, "duplicate id 'CODE-1'")


def test_unknown_check_kind_is_rejected(project):
    text = VALID.replace('check = "command"', 'check = "vibes"')
    assert_rejected(project, text, "unknown check 'vibes'")


def test_unknown_fields_are_rejected_so_typos_do_not_pass_silently(project):
    text = VALID.replace("required = true", "required = true\nrequried = false")
    assert_rejected(project, text, "requried: unknown field")


def test_all_problems_are_reported_at_once(project):
    text = VALID.replace('template = "code"', 'template = "poetry"').replace(
        'check = "command"', 'check = "vibes"'
    )
    assert_rejected(project, text, "template: required, one of", "unknown check")


def test_name_must_match_the_file_name(project):
    assert_rejected(project, VALID.replace('name = "code"', 'name = "docs"'), "name:")


def test_broken_toml_is_rejected(project):
    assert_rejected(project, VALID + "\nthis is not toml\n", "not valid TOML")


def test_invalid_timeout_is_rejected(project):
    assert_rejected(project, command_contract("true", timeout=0), "timeout: must be")


def test_pattern_and_judgment_criteria_are_validated_too(project):
    text = VALID + (
        '\n[[criteria]]\nid = "P-1"\ntext = "No dashes"\ncheck = "pattern"\n'
        'required = true\nforbid = "(unclosed"\n'
        '\n[[criteria]]\nid = "P-2"\ntext = "Short"\ncheck = "pattern"\n'
        "required = true\nmax_words = 400\nmax_lines = 10\n"
        '\n[[criteria]]\nid = "J-1"\ntext = "Clear"\ncheck = "judgment"\n'
        'required = true\ncommand = "true"\n'
    )
    assert_rejected(
        project,
        text,
        "forbid: invalid regular expression",
        "needs exactly one of",
        "(J-1).command: unknown field",
    )


def test_reviewers_and_stop_rule_default_and_are_validated(project):
    full = VALID.replace(
        'paths = ["**"]',
        'paths = ["**"]\n\n[reviewers]\ncount = 2\nmodels = ["opus", "sonnet"]\n'
        "\n[stop]\nmax_rounds = 5\nstall_rounds = 2\n",
    )
    project.write_contract("code", full)
    project.ok("round", "start")

    broken = full.replace("count = 2", "count = 3").replace(
        "stall_rounds = 2", "stall_rounds = 0"
    )
    project.write_contract("code", broken)
    error = project.fail("round", "start")
    found = problems(error)
    assert any("reviewers.models" in p for p in found), found
    assert any("stop.stall_rounds" in p for p in found), found


def test_project_without_contract_reports_no_contract(project):
    assert project.fail("round", "start")["code"] == "no_contract"


def test_several_contracts_need_an_explicit_choice(project):
    project.write_contract("code", VALID)
    project.write_contract("docs", VALID.replace('name = "code"', 'name = "docs"'))

    error = project.fail("round", "start")
    assert error["code"] == "contract_ambiguous"
    assert error["details"]["contracts"] == ["code", "docs"]

    assert project.ok("round", "start", "--contract", "docs")["contract"] == "docs"
    missing = project.fail("round", "start", "--contract", "prose")
    assert missing["code"] == "contract_not_found"


@pytest.mark.parametrize("value", ["inf", "nan", "-inf", "1" + "0" * 400])
def test_timeout_must_be_finite(project, value):
    text = command_contract("true") + f"timeout = {value}\n"
    assert_rejected(project, text, "timeout: must be")


def test_criterion_text_of_the_wrong_type_is_a_contract_problem(project):
    text = VALID.replace('text = "Tests pass"', "text = 2026-10-18")
    assert_rejected(project, text, ".text: required")
