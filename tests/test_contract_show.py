"""contract show: the current contract, readable, with its recorded version."""

from conftest import command_contract, proposal


def test_show_lists_criteria_with_check_and_required_reviewers_and_stop_rule(project):
    project.ok("contract", "create", stdin=proposal(project))

    shown = project.ok("contract", "show")

    contract = shown["contract"]
    assert [(c["id"], c["check"], c["required"]) for c in contract["criteria"]] == [
        ("CODE-1", "command", True),
        ("CODE-2", "command", True),
        ("CODE-3", "judgment", True),
        ("CODE-4", "judgment", False),
    ]
    assert contract["criteria"][0]["params"] == {"command": "true", "timeout": 600}
    assert contract["reviewers"] == {"count": 2, "models": ["inherit"]}
    assert contract["stop"] == {"max_rounds": 5, "stall_rounds": 2, "gate": "warn"}
    assert contract["verifier"] == {"model": "inherit", "max_runs": 5}
    assert shown["version"] == 1
    assert shown["status"] == "recorded"


def test_a_contract_edited_by_hand_shows_as_changed(project):
    project.ok("contract", "create", stdin=proposal(project))
    path = project.root / ".judicata" / "contracts" / "code.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("max_rounds = 5", "max_rounds = 9"),
        encoding="utf-8",
    )

    shown = project.ok("contract", "show")

    assert shown["status"] == "changed"
    assert shown["version"] is None
    assert shown["latest_version"] == 1
    assert shown["contract"]["stop"]["max_rounds"] == 9


def test_a_contract_without_recorded_version_shows_as_unrecorded(project):
    project.write_contract("code", command_contract("true"))

    shown = project.ok("contract", "show")

    assert shown["status"] == "unrecorded"
    assert shown["version"] is None


def test_show_without_contract_or_with_several(project):
    assert project.fail("contract", "show")["code"] == "no_contract"

    project.ok("contract", "create", stdin=proposal(project))
    project.ok("contract", "create", stdin=proposal(project, "docs"))

    assert project.fail("contract", "show")["code"] == "contract_ambiguous"
    shown = project.ok("contract", "show", "--contract", "docs")
    assert shown["contract"]["template"] == "docs"
    assert shown["version"] == 1
