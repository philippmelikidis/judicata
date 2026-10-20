"""The interview ends in contract create: validated, stored as version 1, frozen."""

import json
import tomllib

import pytest
from conftest import proposal


def test_a_created_contract_is_used_by_the_next_review(project):
    draft = proposal(project)

    created = project.ok("contract", "create", stdin=draft)

    assert created["contract"]["name"] == "code"
    assert created["version"] == 1
    assert (project.root / ".judicata" / "contracts" / "code.toml").is_file()
    closed = project.review()
    assert closed["contract"] == "code"
    assert set(closed["passed"]) == {"CODE-1", "CODE-2"}


@pytest.mark.parametrize("template", ["code", "docs", "prose"])
def test_every_template_proposal_becomes_a_valid_contract(project, template):
    created = project.ok("contract", "create", stdin=proposal(project, template))

    assert created["contract"]["template"] == template
    assert project.ok("round", "start")["contract"] == template


def stored(project, name: str = "code") -> dict:
    path = project.root / ".judicata" / "contracts" / f"{name}.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_the_interview_edits_the_proposal_and_the_template_stays_a_proposal(project):
    draft = proposal(project)
    draft["criteria"] = [c for c in draft["criteria"] if c["id"] != "CODE-4"]
    draft["criteria"][2]["required"] = False
    draft["criteria"].append(
        {
            "id": "CODE-5",
            "text": "No print statements",
            "check": "pattern",
            "required": True,
            "forbid": r"\bprint\(",
        }
    )

    project.ok("contract", "create", stdin=draft)

    ids = [c["id"] for c in stored(project)["criteria"]]
    assert ids == ["CODE-1", "CODE-2", "CODE-3", "CODE-5"]
    assert stored(project)["criteria"][2]["required"] is False
    again = project.ok("template", "show", "--template", "code")["proposal"]
    assert [c["id"] for c in again["criteria"]] == [
        "CODE-1",
        "CODE-2",
        "CODE-3",
        "CODE-4",
    ]


def test_reviewers_and_stop_rule_default_and_are_written_into_the_contract(project):
    draft = proposal(project)
    del draft["reviewers"], draft["stop"]

    project.ok("contract", "create", stdin=draft)

    assert stored(project)["reviewers"] == {"count": 2, "models": ["inherit"]}
    assert stored(project)["stop"] == {
        "max_rounds": 5,
        "stall_rounds": 2,
        "gate": "warn",
    }


def test_chosen_reviewers_and_stop_rule_are_kept(project):
    draft = proposal(
        project,
        reviewers={"count": 2, "models": ["opus", "sonnet"]},
        stop={"max_rounds": 3, "stall_rounds": 1},
    )

    project.ok("contract", "create", stdin=draft)

    assert stored(project)["reviewers"] == {"count": 2, "models": ["opus", "sonnet"]}
    assert stored(project)["stop"] == {
        "max_rounds": 3,
        "stall_rounds": 1,
        "gate": "warn",
    }


def test_an_invalid_proposal_is_rejected_and_nothing_is_written(project):
    draft = proposal(project)
    draft["criteria"].append(dict(draft["criteria"][0]))
    draft["criteria"][1]["check"] = "vibes"

    error = project.fail("contract", "create", stdin=draft)

    assert error["code"] == "invalid_contract"
    found = [f"{p['field']}: {p['problem']}" for p in error["details"]["problems"]]
    assert any("duplicate id 'CODE-1'" in p for p in found), found
    assert any("unknown check 'vibes'" in p for p in found), found
    assert not (project.root / ".judicata").exists()


def test_an_existing_contract_is_not_overwritten(project):
    project.ok("contract", "create", stdin=proposal(project))
    before = (project.root / ".judicata" / "contracts" / "code.toml").read_bytes()
    events = len(project.ledger_events())

    draft = proposal(project)
    draft["criteria"] = draft["criteria"][:1]
    error = project.fail("contract", "create", stdin=draft)

    assert error["code"] == "contract_exists"
    after = (project.root / ".judicata" / "contracts" / "code.toml").read_bytes()
    assert after == before
    assert len(project.ledger_events()) == events


def test_storing_records_a_version_with_a_hash_per_criterion_and_approves_it(
    project,
):
    created = project.ok("contract", "create", stdin=proposal(project))

    events = project.ledger_events()
    assert [e["type"] for e in events] == ["contract_version", "approval_logged"]
    version, approved = (e["data"] for e in events)
    assert version["contract"] == "code" and version["version"] == 1
    hashes = {c["id"]: c["hash"] for c in version["criteria"]}
    assert set(hashes) == {"CODE-1", "CODE-2", "CODE-3", "CODE-4"}
    assert len(set(hashes.values())) == 4
    assert approved["contract_hash"] == version["contract_hash"]
    assert created["approved"] is True

    started = project.ok("round", "start")
    assert {c["id"]: c["hash"] for c in started["criteria"]} == hashes


def test_validate_checks_a_proposal_without_writing(project):
    draft = proposal(project)
    del draft["stop"]

    checked = project.ok("contract", "validate", stdin=draft)

    assert checked["contract"]["stop"] == {
        "max_rounds": 5,
        "stall_rounds": 2,
        "gate": "warn",
    }
    assert checked["exists"] is False
    assert not (project.root / ".judicata").exists()
    draft["template"] = "poetry"
    assert project.fail("contract", "validate", stdin=draft)["code"] == (
        "invalid_contract"
    )


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '"code"', b"\xff\xfe{}"])
def test_input_that_is_not_a_json_object_is_rejected(project, stdin):
    error = project.fail("contract", "create", stdin=stdin)

    assert error["code"] == "invalid_input"
    assert not (project.root / ".judicata").exists()


def test_text_with_quotes_backslashes_and_unicode_is_stored_exactly(project):
    text = (
        'Say "done" when C:\\path\\ exists,\nnot before; caf\u00e9 \u2192 ok\t\x01\x7f'
    )
    draft = proposal(project)
    draft["criteria"][2]["text"] = text
    draft["criteria"][1]["command"] = 'printf "%s\\n" \'a b\' | grep -q "a"'
    draft["criteria"].append(
        {
            "id": "CODE-5",
            "text": "No tabs",
            "check": "pattern",
            "required": False,
            "forbid": "\\t|[\\u0000-\\u0008]",
        }
    )

    project.ok("contract", "create", stdin=draft)

    stored = tomllib.loads(
        (project.root / ".judicata" / "contracts" / "code.toml").read_text("utf-8")
    )
    assert stored["criteria"][2]["text"] == text
    shown = project.ok("contract", "show")
    # control characters: shown cleaned and marked (untrusted.py), stored exactly
    assert "\x01" not in shown["contract"]["criteria"][2]["text"]
    assert any("hidden_characters" in w["reasons"] for w in shown["warnings"])
    shown = shown["contract"]
    assert shown["criteria"][1]["params"]["command"] == draft["criteria"][1]["command"]
    assert shown["criteria"][4]["params"]["forbid"] == "\\t|[\\u0000-\\u0008]"
    assert project.review()["passed"][:2] == ["CODE-1", "CODE-2"]


@pytest.mark.parametrize(
    ("where", "value"),
    [("text", "broken \ud800 unicode"), ("timeout", 10**400), ("timeout", 2**64)],
)
def test_values_toml_cannot_hold_are_contract_problems(project, where, value):
    draft = proposal(project)
    draft["criteria"][0][where] = value

    error = project.fail("contract", "create", stdin=json.dumps(draft))

    assert error["code"] == "invalid_contract", error
    assert any(where in p["field"] for p in error["details"]["problems"]), error
    assert not (project.root / ".judicata").exists()


def test_a_failed_ledger_write_leaves_no_contract_behind(project):
    project.ledger.parent.mkdir(parents=True)
    project.ledger.write_text("", encoding="utf-8")
    (project.root / ".gitattributes").write_text(
        ".judicata/ledger.jsonl merge=union\n", encoding="utf-8"
    )
    project.ledger.chmod(0o444)
    try:
        project.fail("contract", "create", stdin=proposal(project))
    finally:
        project.ledger.chmod(0o644)

    assert not (project.root / ".judicata" / "contracts" / "code.toml").exists()
    assert project.ledger_events() == []
    assert not list(project.state_dir.rglob("*.json"))
    assert project.ok("contract", "create", stdin=proposal(project))["version"] == 1
