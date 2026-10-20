"""Stop gate, warn mode (#12): at the end of every agent turn the Stop hook asks
the core whether the work under a contract is reviewed and done.

`judicata.py hook stop` reads the Stop hook input (session, transcript_path,
stop_hook_active, cwd) and prints {"systemMessage": "<one line>"} when

  (a) the latest review of the session for a contract (a round the session
      named with --round) did not end in a final result, or
  (b) files in a contract's scope were written (or removed) during the session
      after the last finished review saw them, their content differs from what
      it saw and no review of them ran since.

The agent is never stopped. Sessions that touched no contract, projects
without .judicata/ and every internal error give no output and exit 0.
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import SCRIPT

ROOT = SCRIPT.parent.parent


def contract(
    *, max_rounds=5, stall_rounds=2, required=True, name="code", gate=None
) -> str:
    """CODE-1 fails while the file `fail-<name>` exists at the project root;
    the contract covers src/<name>. gate: the [stop] gate mode, None = default."""
    return (
        f'name = "{name}"\n'
        'template = "code"\n'
        f'paths = ["src/{name}"]\n'
        "\n[stop]\n"
        f"max_rounds = {max_rounds}\n"
        f"stall_rounds = {stall_rounds}\n"
        + ("" if gate is None else f'gate = "{gate}"\n')
        + "\n[[criteria]]\n"
        'id = "CODE-1"\n'
        'text = "Tests pass"\n'
        'check = "command"\n'
        f"required = {'true' if required else 'false'}\n"
        f'command = "test ! -e fail-{name}"\n'
    )


def setup(project, **kwargs) -> Path:
    name = kwargs.get("name", "code")
    source = project.root / "src" / name / "app.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('v1')\n", encoding="utf-8")
    project.write_contract(name, contract(**kwargs))
    age(project)
    return source


def age(project) -> None:
    """Date the files and folders written so far a minute back, so that they
    lie clearly before the session (the gate allows 2 s for coarse clocks)."""
    then = time.time() - 60
    for path in [project.root, *project.root.rglob("*")]:
        os.utime(path, (then, then), follow_symlinks=False)


def failing(project, name="code", on=True) -> None:
    marker = project.root / f"fail-{name}"
    if on:
        marker.write_text("x\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()


def review(project, *args) -> str:
    """One round, start to close; returns the round id."""
    round_id = project.ok("round", "start", *args)["round"]
    project.ok("round", "check", "--round", round_id)
    project.ok("round", "close", "--round", round_id)
    return round_id


def stamp(when: float) -> str:
    text = datetime.fromtimestamp(when, UTC).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def session(project, *round_ids, start=None, codex=False, cwd=None) -> dict:
    """The Stop hook input of a session that ran commands on these rounds.

    The transcript is written like Claude Code writes it (or like a Codex
    rollout with codex=True); the session started at `start` (default: a few
    seconds ago).
    """
    begun = stamp(time.time() - 5 if start is None else start)
    command = "python3 judicata.py round close --project . --round {}"
    if codex:
        lines = [
            {"timestamp": begun, "type": "session_meta", "payload": {"id": "c1"}},
            *(
                {
                    "timestamp": begun,
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": command.format(r)}),
                    },
                }
                for r in round_ids
            ),
        ]
    else:
        lines = [
            {"type": "user", "timestamp": begun, "message": {"content": "review"}},
            *(
                {
                    "type": "assistant",
                    "timestamp": begun,
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": command.format(r)},
                            }
                        ]
                    },
                }
                for r in round_ids
            ),
        ]
    path = project.root.parent / "transcript.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return {
        "session_id": "s1",
        "transcript_path": str(path),
        "cwd": str(cwd or project.root),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "permission_mode": "default",
    }


def gate(project, hook_input) -> str | None:
    """The warning line of the Stop hook, or None when it prints nothing."""
    code, stdout, stderr = project.hook("stop", stdin=hook_input)
    assert code == 0, stderr
    if stdout == "":
        return None
    payload = json.loads(stdout)
    assert set(payload) == {"systemMessage"}  # a warning only, never a decision
    message = payload["systemMessage"]
    assert "\n" not in message
    assert message.startswith("judicata")
    return message


def edit(path: Path, text: str = "print('v2')\n") -> None:
    path.write_text(text, encoding="utf-8")
    now = time.time()
    os.utime(path, (now, now))


# (a) the latest review of the session


def test_a_review_that_ended_in_continue_warns_with_the_open_blockers(project):
    setup(project)
    failing(project)
    round_id = review(project)

    message = gate(project, session(project, round_id))

    assert message is not None
    assert "code" in message
    assert "continue" in message
    assert "1 open blocker" in message
    assert "CODE-1" in message
    assert "/judicata:review" in message


def test_a_review_that_was_not_closed_warns(project):
    setup(project)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)

    message = gate(project, session(project, round_id))

    assert message is not None
    assert "not closed" in message


def test_only_the_latest_review_of_the_session_counts(project):
    setup(project)
    failing(project)
    first = review(project)
    failing(project, on=False)
    second = review(project)

    assert gate(project, session(project, first, second)) is None


def test_a_later_final_review_of_another_session_releases_the_gate(project):
    setup(project)
    failing(project)
    ours = review(project)
    failing(project, on=False)
    review(project)  # another session finished the work

    assert gate(project, session(project, ours)) is None


def test_a_round_the_session_only_started_counts(project):
    setup(project)
    round_id = project.ok("round", "start")["round"]
    hook_input = session(project)
    answer = json.dumps({"ok": True, "round": round_id, "contract": "code"}, indent=2)
    with open(hook_input["transcript_path"], "a", encoding="utf-8") as handle:
        result = {"type": "tool_result", "content": answer}
        line = {"type": "user", "message": {"content": [result]}}
        handle.write(json.dumps(line) + "\n")

    message = gate(project, hook_input)

    assert message is not None and "not closed" in message


def test_a_later_final_review_of_another_scope_does_not_release(project):
    source = setup(project)
    (source.parent / "other.py").write_text("y = 1\n", encoding="utf-8")
    failing(project)
    ours = review(project, "--for", "src/code/app.py")
    failing(project, on=False)
    review(project, "--for", "src/code/other.py")  # done, but elsewhere

    message = gate(project, session(project, ours))

    assert message is not None and "continue" in message


def test_a_focused_review_is_no_final_result(project):
    setup(project)
    round_id = review(project, "--criteria", "CODE-1", "--categories", "")

    message = gate(project, session(project, round_id))

    assert message is not None
    assert "focus clear" in message


def _done(project):
    return [review(project)]


def _accepted_risks(project):
    failing(project)
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    for entry in project.ok("finding", "list", "--round", round_id)["findings"]:
        project.ok(
            "finding", "decide", "--round", round_id, "--fingerprint",
            entry["fingerprint"], "--decision", "accept", "--reason", "known",
            "--reopen-if", "it matters",
        )  # fmt: skip
    closed = project.ok("round", "close", "--round", round_id)
    return [round_id], closed


def _done_with_accepted_risks(project):
    ids, closed = _accepted_risks(project)
    assert closed["outcome"] == "done"
    return ids


def _finished_despite_unmet(project):
    ids, closed = _accepted_risks(project)
    assert closed["outcome"] == "required_unmet"
    project.ok("round", "finish", "--round", ids[0], "--confirm", closed["unmet_hash"])
    return ids


def _round_limit(project):
    failing(project)
    return [review(project)]


def _not_converged(project):
    failing(project)
    return [review(project), review(project)]


@pytest.mark.parametrize(
    ("limits", "play", "outcome"),
    [
        ({}, _done, "done"),
        ({"required": False}, _done_with_accepted_risks, "done"),
        ({}, _finished_despite_unmet, "done_with_unmet_required"),
        ({"max_rounds": 1}, _round_limit, "round_limit"),
        ({"stall_rounds": 1}, _not_converged, "not_converged"),
    ],
)
def test_a_final_result_gives_no_warning(project, limits, play, outcome):
    setup(project, **limits)
    round_ids = play(project)
    [entry] = project.ok("status")["contracts"]
    assert entry["outcome"] == outcome

    assert gate(project, session(project, *round_ids)) is None


# (b) files in scope changed since the last finished review


def test_a_changed_file_after_done_warns_when_no_review_ran_since(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source)

    message = gate(project, hook_input)

    assert message is not None
    assert "code" in message
    assert "src/code/app.py" in message
    assert "written in this session" in message
    assert "/judicata:review" in message


def test_a_new_file_in_scope_after_done_warns(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source.parent / "extra.py")

    message = gate(project, hook_input)

    assert message is not None
    assert "src/code/extra.py" in message


def test_the_session_that_ran_the_review_and_changed_files_after_it_is_warned(
    project,
):
    source = setup(project)
    round_id = review(project)
    hook_input = session(project, round_id, start=time.time() - 1)
    edit(source)

    message = gate(project, hook_input)

    assert message is not None
    assert "src/code/app.py" in message


def test_files_changed_before_the_session_are_not_its_business(project):
    source = setup(project)
    review(project)
    start = time.time()
    source.write_text("print('v2')\n", encoding="utf-8")
    os.utime(source, (start - 100, start - 100))

    assert gate(project, session(project, start=start)) is None


def test_a_file_written_back_unchanged_gives_no_warning(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source, "print('v1')\n")

    assert gate(project, hook_input) is None


def test_a_changed_file_outside_every_contract_gives_no_warning(project):
    setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(project.root / "notes.txt")

    assert gate(project, hook_input) is None


def test_a_review_since_the_last_done_silences_the_changed_files(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source)
    project.ok("round", "start")  # another session reviews again

    assert gate(project, hook_input) is None


def test_without_a_finished_review_changed_files_give_no_warning(project):
    source = setup(project)
    hook_input = session(project, start=time.time() - 1)
    edit(source)

    assert gate(project, hook_input) is None


def test_a_removed_file_after_done_warns(project):
    source = setup(project)
    (source.parent / "extra.py").write_text("x = 1\n", encoding="utf-8")
    age(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    (source.parent / "extra.py").unlink()

    message = gate(project, hook_input)

    assert message is not None
    assert "removed or renamed" in message


def test_removing_the_only_file_of_the_scope_warns(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 1)
    source.unlink()
    source.parent.rmdir()

    assert gate(project, hook_input) is not None


def test_a_later_narrowed_done_does_not_hide_changes_elsewhere(project):
    source = setup(project)
    other = source.parent / "other.py"
    other.write_text("y = 1\n", encoding="utf-8")
    age(project)
    review(project)
    review(project, "--for", "src/code/app.py")  # done for app.py only
    hook_input = session(project, start=time.time() - 1)
    edit(other)

    message = gate(project, hook_input)

    assert message is not None
    assert "src/code/other.py" in message


def test_a_file_reviewed_again_later_in_the_session_is_not_named(project):
    source = setup(project)
    review(project)
    hook_input = session(project, start=time.time() - 5)
    edit(source)
    time.sleep(3)  # longer than the slack for coarse file times
    review(project)  # this session reviewed the change

    assert gate(project, hook_input) is None


def test_the_line_names_only_files_written_in_the_session(project):
    source = setup(project)
    other = source.parent / "other.py"
    other.write_text("y = 1\n", encoding="utf-8")
    age(project)
    review(project)
    start = time.time()
    source.write_text("print('changed before the session')\n", encoding="utf-8")
    os.utime(source, (start - 100, start - 100))
    hook_input = session(project, start=start)
    edit(other, "y = 1\n")  # written back unchanged

    message = gate(project, hook_input)

    # the scope differs (app.py, before the session), this session wrote other.py
    assert message is not None
    assert "written in this session: src/code/other.py" in message
    assert "app.py" not in message


# both at once, several contracts


def test_one_line_names_every_touched_contract_and_only_those(project):
    setup(project, name="code")
    docs = setup(project, name="docs")
    setup(project, name="lib")
    failing(project, "code")
    failing(project, "lib")
    code_round = review(project, "--contract", "code")
    review(project, "--contract", "docs")
    review(project, "--contract", "lib")  # not this session's round
    hook_input = session(project, code_round, start=time.time() - 1)
    edit(docs)

    message = gate(project, hook_input)

    assert message is not None
    assert "code" in message and "continue" in message
    assert "docs" in message and "src/docs/app.py" in message
    assert "lib" not in message


# silence


def test_without_judicata_the_gate_prints_nothing(project):
    hook_input = session(project, "0" * 32)
    edit(project.root / "app.py")

    assert gate(project, hook_input) is None
    assert not (project.root / ".judicata").exists()


def test_a_session_that_touched_no_contract_gets_no_warning(project):
    setup(project)
    failing(project)
    review(project)  # open blockers, but not from this session

    assert gate(project, session(project)) is None


@pytest.mark.parametrize(
    "broken",
    [
        "",
        "not json",
        "[1, 2]",
        {"cwd": "/nonexistent/dir"},
        {"transcript_path": None},
        {"transcript_path": 7},
        {"transcript_path": "/nonexistent/transcript.jsonl"},
    ],
)
def test_bad_input_never_fails_the_turn(project, broken):
    setup(project)
    failing(project)
    round_id = review(project)
    hook_input = session(project, round_id)
    if isinstance(broken, dict):
        broken = {**hook_input, **broken}

    code, stdout, _ = project.hook("stop", stdin=broken)

    assert code == 0
    assert stdout == ""


def test_a_broken_ledger_gives_no_output(project):
    setup(project)
    failing(project)
    round_id = review(project)
    with project.ledger.open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")

    code, stdout, _ = project.hook("stop", stdin=session(project, round_id))

    assert code == 0
    assert stdout == ""


def test_a_transcript_without_a_start_time_still_finds_the_reviews(project):
    setup(project)
    failing(project)
    round_id = review(project)
    hook_input = session(project, round_id)
    path = Path(hook_input["transcript_path"])
    path.write_text(
        path.read_text(encoding="utf-8").replace('"timestamp"', '"time"'),
        encoding="utf-8",
    )

    message = gate(project, hook_input)

    assert message is not None and "continue" in message


# hosts


def test_a_codex_transcript_works_the_same(project):
    setup(project)
    failing(project)
    round_id = review(project)

    message = gate(project, session(project, round_id, codex=True))

    assert message is not None and "continue" in message


def test_a_session_in_a_subdirectory_finds_the_project(project):
    setup(project)
    failing(project)
    round_id = review(project)

    message = gate(project, session(project, round_id, cwd=project.root / "src"))

    assert message is not None


def test_hooks_json_registers_the_gate_on_stop(project):
    setup(project)
    failing(project)
    round_id = review(project)
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    [entry] = config["hooks"]["Stop"]
    [hook] = entry["hooks"]
    assert hook["type"] == "command"

    proc = subprocess.run(
        hook["command"],
        shell=True,
        check=False,
        capture_output=True,
        input=json.dumps(session(project, round_id)).encode("utf-8"),
        env={
            **os.environ,
            "CLAUDE_PLUGIN_ROOT": str(ROOT),
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "JUDICATA_STATE_DIR": str(project.state_dir),
        },
        cwd=project.root,
        timeout=60,
    )

    assert proc.returncode == 0
    assert "continue" in json.loads(proc.stdout)["systemMessage"]


# sessions without a transcript (claude -p --no-session-persistence)


def started_session(project, session_id="s1") -> dict:
    """Run the SessionStart hook; return the Stop input without a transcript."""
    code, _, _ = project.hook(
        stdin={
            "session_id": session_id,
            "hook_event_name": "SessionStart",
            "source": "startup",
            "cwd": str(project.root),
        }
    )
    assert code == 0
    return {
        "session_id": session_id,
        "transcript_path": str(project.root.parent / "never-written.jsonl"),
        "cwd": str(project.root),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }


def test_without_a_transcript_the_rounds_since_the_session_start_count(project):
    setup(project)
    failing(project)
    review(project)  # before this session
    hook_input = started_session(project)
    assert gate(project, hook_input) is None

    review(project)

    message = gate(project, hook_input)
    assert message is not None and "continue" in message


def test_without_a_transcript_changed_files_after_done_warn(project):
    source = setup(project)
    review(project)
    hook_input = started_session(project)
    edit(source)

    message = gate(project, hook_input)

    assert message is not None and "src/code/app.py" in message


def test_a_later_session_start_of_the_same_session_keeps_the_first(project):
    source = setup(project)
    review(project)
    hook_input = started_session(project)
    edit(source)
    time.sleep(3)  # longer than the slack for coarse file times
    started_session(project)  # compact or resume: the same session goes on

    assert gate(project, hook_input) is not None


def test_another_session_has_its_own_start(project):
    source = setup(project)
    review(project)
    started_session(project, "other")
    edit(source)
    time.sleep(3)

    assert gate(project, started_session(project, "s1")) is None
