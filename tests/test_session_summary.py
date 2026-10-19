"""session summary: the ledger summary of the SessionStart hook, on demand.

Hosts without hooks (judicata installed as plain skills) get no summary at
the start of a session; the review and status skills ask the core for it.
"""

import json

from conftest import judgment_contract


def hook_context(project) -> str:
    code, stdout, stderr = project.hook()
    assert code == 0, stderr
    return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


def test_without_judicata_there_is_no_summary_and_nothing_is_written(project):
    result = project.ok("session", "summary")

    assert result["found"] is False
    assert result["context"] is None
    assert list(project.root.iterdir()) == []


def test_the_summary_is_the_context_a_new_session_gets_from_the_hook(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    project.review()

    result = project.ok("session", "summary")

    assert result["found"] is True
    assert result["context"] == hook_context(project)
    assert "judicata is set up in this project" in result["context"]
    assert "last round: continue" in result["context"].lower()


def test_a_broken_ledger_still_gives_a_summary(project):
    project.write_contract("code", judgment_contract())
    project.ledger.parent.mkdir(parents=True, exist_ok=True)
    project.ledger.write_text("not json\n", encoding="utf-8")

    result = project.ok("session", "summary")

    assert result["found"] is True
    assert "ledger_corrupt" in result["context"]
