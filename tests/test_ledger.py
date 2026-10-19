"""The ledger only grows; state comes from replaying it, duplicates count once."""

import json

import pytest
from conftest import command_contract


def test_a_round_appends_started_result_and_closed_events(project):
    project.write_contract("code", command_contract("true"))

    project.review()

    events = project.ledger_events()
    assert [e["type"] for e in events] == [
        "round_started",
        "criterion_result",
        "round_closed",
    ]
    for event in events:
        assert set(event) >= {"id", "ts", "type", "data"}
        assert event["ts"].endswith("Z")
    assert len({e["id"] for e in events}) == len(events)


def test_later_rounds_append_and_never_rewrite_earlier_lines(project):
    project.write_contract("code", command_contract("true"))
    project.review()
    before = project.ledger.read_bytes()

    project.write_contract("code", command_contract("false"))
    project.review()
    after = project.ledger.read_bytes()

    assert after.startswith(before)
    # the failed criterion of the second round adds its finding
    assert len(after.splitlines()) == 2 * len(before.splitlines()) + 1


def test_duplicated_lines_after_a_union_merge_count_once(project):
    project.write_contract("code", command_contract("false"))
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    lines = project.ledger.read_text(encoding="utf-8")
    project.ledger.write_text(lines + lines, encoding="utf-8")

    rerun = project.ok("round", "check", "--round", round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert len(rerun["results"]) == 1
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-1"]
    types = [e["type"] for e in project.ledger_events()]
    assert types.count("criterion_result") == 2  # the duplicate line plus original
    assert types[-1] == "round_closed"


def test_checking_again_does_not_rerun_commands(project):
    project.write_contract("code", command_contract("echo run >> runs.txt"))
    round_id = project.ok("round", "start")["round"]

    project.ok("round", "check", "--round", round_id)
    project.ok("round", "check", "--round", round_id)

    assert (project.root / "runs.txt").read_text().splitlines() == ["run"]


def test_closed_round_cannot_be_checked_or_closed_again(project):
    project.write_contract("code", command_contract("true"))
    round_id = project.review()["round"]

    assert project.fail("round", "check", "--round", round_id)["code"] == "round_closed"
    assert project.fail("round", "close", "--round", round_id)["code"] == "round_closed"


def test_unknown_round_is_reported(project):
    project.write_contract("code", command_contract("true"))
    error = project.fail("round", "close", "--round", "nope")
    assert error["code"] == "round_not_found"


def test_contract_changed_after_start_refuses_to_check(project):
    project.write_contract("code", command_contract("true"))
    round_id = project.ok("round", "start")["round"]
    project.write_contract("code", command_contract("rm -rf important"))

    error = project.fail("round", "check", "--round", round_id)
    assert error["code"] == "contract_changed"


def test_unreadable_ledger_line_is_reported_not_ignored(project):
    project.write_contract("code", command_contract("true"))
    round_id = project.ok("round", "start")["round"]
    with project.ledger.open("a", encoding="utf-8") as handle:
        handle.write("<<<<<<< HEAD\n")

    error = project.fail("round", "close", "--round", round_id)
    assert error["code"] == "ledger_corrupt"
    assert error["details"]["line"] == 2


def test_event_with_missing_fields_is_reported_as_corrupt_ledger(project):
    project.write_contract("code", command_contract("true"))
    round_id = project.ok("round", "start")["round"]
    with project.ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"id": "broken", "ts": "2026-01-01T00:00:00.000000Z", '
            '"type": "round_started", "data": {}}\n'
        )

    error = project.fail("round", "close", "--round", round_id)
    assert error["code"] == "ledger_corrupt"
    assert error["details"]["event"] == "broken"


@pytest.mark.parametrize(
    "ts",
    [None, 42, "yesterday", "9999-12-31T23:59:59-01:00"],
    ids=["missing", "number", "not-a-date", "beyond-utc-range"],
)
def test_event_without_a_valid_timestamp_is_reported_as_corrupt_ledger(project, ts):
    project.write_contract("code", command_contract("true"))
    round_id = project.ok("round", "start")["round"]
    event = {"id": "late", "type": "round_closed", "data": {"round": round_id}}
    if ts is not None:
        event["ts"] = ts
    with project.ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")

    error = project.fail("round", "close", "--round", round_id)
    assert error["code"] == "ledger_corrupt"
    assert error["details"]["line"] == 2


def test_replay_follows_the_timestamps_not_the_line_order(project):
    """After a union merge the lines of two branches can come in either order."""
    project.write_contract("code", command_contract("echo run >> runs.txt; false"))
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    lines = project.ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    project.ledger.write_text("".join(reversed(lines)), encoding="utf-8")

    rerun = project.ok("round", "check", "--round", round_id)
    closed = project.ok("round", "close", "--round", round_id)

    assert len(rerun["results"]) == 1
    assert (project.root / "runs.txt").read_text().splitlines() == ["run"]
    assert [b["criterion"] for b in closed["blockers"]] == ["CODE-1"]
