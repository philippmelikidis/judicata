"""SessionStart hook: a new session gets a short summary of the ledger.

The hook runs the core with the hook input (JSON with cwd) on stdin. With a
.judicata/ directory it prints hookSpecificOutput.additionalContext below
10,000 characters; without one it prints nothing. It never fails the session.
"""

import json
import shlex

from conftest import SCRIPT, judgment_contract, proposal, reviewer_output

LIMIT = 10_000
REASON = ("--reason", "Callers never pass zero.")
REOPEN = ("--reopen-if", "ratio gets a caller that passes user input")


def context_of(project, **kwargs) -> str:
    code, stdout, stderr = project.hook(**kwargs)
    assert code == 0, stderr
    payload = json.loads(stdout)
    output = payload["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert set(payload) == {"hookSpecificOutput"}
    return output["additionalContext"]


def output(*findings, fixture="findings") -> str:
    data = json.loads(reviewer_output(fixture))
    data["findings"] = list(findings)
    return json.dumps(data)


BUG = json.loads(reviewer_output("findings"))["findings"][0]
AREA = {
    **BUG,
    "category": "maintainability",
    "severity": "note",
    "line": 11,
    "symbol": "area",
    "quote": "return math.pi * self.radius**2",
    "summary": "area could use a docstring.",
}


def test_without_judicata_the_hook_prints_nothing_and_succeeds(project):
    code, stdout, _ = project.hook()

    assert code == 0
    assert stdout == ""
    assert not (project.root / ".judicata").exists()


def test_bad_input_never_fails_the_session(project, tmp_path):
    for stdin in ("", "not json", "[1, 2]", {"cwd": str(tmp_path / "missing")}):
        code, stdout, _ = project.hook(stdin=stdin)
        assert code == 0
        assert stdout == ""


def test_an_unknown_hook_event_prints_nothing(project):
    project.write_contract("code", judgment_contract())

    code, stdout, _ = project.hook(event="post-everything")

    assert code == 0
    assert stdout == ""


def test_the_context_names_contracts_findings_register_and_last_outcome(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run in runs:
        project.submit(round_id, run, output(BUG, AREA))
    entries = project.ok("finding", "list", "--round", round_id)["findings"]
    bug = next(e for e in entries if e["label"] == "src/shapes.py::ratio")
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        bug["fingerprint"], "--decision", "reject", *REASON, *REOPEN,
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)

    context = context_of(project)

    assert str(project.root) in context
    assert "/judicata:review" in context
    assert "code" in context and "JUD-1" in context
    assert "last round: done" in context.lower()
    assert "src/shapes.py::ratio" in context
    assert REASON[1] in context and REOPEN[1] in context
    assert "src/shapes.py::Circle.area" in context  # the open note
    assert len(context) < LIMIT


def test_open_blockers_are_listed(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    round_id, runs = project.judged_round()
    for run in runs:
        project.submit(round_id, run, output(BUG))
    project.ok("round", "close", "--round", round_id)

    context = context_of(project)

    assert "blocker" in context.lower()
    assert "ratio(1, 0) raises ZeroDivisionError." in context
    assert "last round: continue" in context.lower()


def test_a_session_in_a_subdirectory_finds_the_project(project):
    project.write_contract("code", judgment_contract())
    (project.root / "src" / "deep").mkdir(parents=True)

    context = context_of(project, cwd=project.root / "src" / "deep")

    assert str(project.root) in context


def test_the_context_names_the_core_command_for_hosts_without_the_plugin_root(project):
    # Codex runs the hook with CLAUDE_PLUGIN_ROOT set, but not the model's shell
    # commands, and it does not replace the variable in skill texts (#19).
    project.write_contract("code", judgment_contract())

    context = context_of(project)

    assert f"python3 {shlex.quote(str(SCRIPT))}" in context
    assert context.index(str(SCRIPT)) < context.index("<judicata-data>")


def test_a_contract_without_rounds_is_named(project):
    project.write_contract("code", judgment_contract())

    context = context_of(project)

    assert "code" in context
    assert "no round yet" in context.lower()


def test_a_broken_ledger_still_gives_a_short_context(project):
    project.write_contract("code", judgment_contract())
    project.ledger.parent.mkdir(parents=True, exist_ok=True)
    project.ledger.write_text("{broken\n", encoding="utf-8")

    context = context_of(project)

    assert "ledger" in context.lower()
    assert "ledger_corrupt" in context
    assert len(context) < LIMIT


def test_an_invalid_contract_is_named_not_fatal(project):
    project.write_contract("code", judgment_contract())
    project.write_contract("broken", "name = 1\n", approve=False)

    context = context_of(project)

    assert "broken" in context
    assert "invalid" in context.lower()


# a large ledger

MANY = 60
RULES = ["TODO", "FIXME", "XXX", "HACK"]


def large_contract() -> str:
    lines = ['name = "code"', 'template = "code"', 'paths = ["src/**"]']
    for number, word in enumerate(RULES, 1):
        lines += [
            "",
            "[[criteria]]",
            f'id = "PAT-{number}"',
            f'text = "No {word} marker may stay anywhere in the shipped source code"',
            'check = "pattern"',
            f"required = {'true' if number <= 2 else 'false'}",
            f'forbid = "{word}"',
        ]
    return "\n".join(lines) + "\n"


def large_source() -> str:
    parts = []
    for number in range(MANY):
        parts.append(  # markers on the def line, the reviewer cites the return
            f"def function_with_a_rather_long_descriptive_name_{number:03}():"
            f"  # {' '.join(RULES)}\n    return {number}\n\n\n"
        )
    return "".join(parts)


def many_findings() -> str:
    """One reviewer reports a correctness blocker in every function."""
    findings = [
        {
            "category": "correctness",
            "severity": "blocker",
            "file": "src/many.py",
            "line": 4 * number + 2,
            "symbol": f"function_with_a_rather_long_descriptive_name_{number:03}",
            "quote": f"return {number}",
            "summary": f"function {number} returns a constant where callers expect "
            "a computed value, so every caller gets the same wrong answer.",
            "evidence": f"Calling it with any state returns {number}.",
        }
        for number in range(45)  # the schema takes at most 50 per run
    ]
    verdict = {
        "id": "JUD-1",
        "passed": True,
        "reason": "Checked.",
        "evidence": [{"file": "src/many.py", "line": 2, "quote": "return 0"}],
    }
    notes = [
        {
            **item,
            "category": "style",
            "severity": "note",
            "summary": f"function {item['line']} has a very long name.",
        }
        for item in findings[40:]
    ]
    return json.dumps({"criteria": [verdict], "findings": findings[:40] + notes})


def test_a_large_ledger_stays_below_the_limit_with_blockers_first(project):
    judged = (
        '\n[[criteria]]\nid = "JUD-1"\ntext = "No known bugs"\n'
        'check = "judgment"\nrequired = true\n'
    )
    text = large_contract().replace(
        'paths = ["src/**"]', 'paths = ["src/**"]\n\n[reviewers]\ncount = 1'
    )
    project.write_contract("code", text + judged)
    (project.root / "src").mkdir()
    (project.root / "src" / "many.py").write_text(large_source(), encoding="utf-8")
    round_id, [run] = project.judged_round()
    code, payload = project.submit(round_id, run, many_findings())
    assert code == 0, payload
    entries = project.ok("finding", "list", "--round", round_id)["findings"]
    optional = [e for e in entries if e.get("criterion") == "PAT-4"][:12]
    long_reason = "This marker is part of a generated block we do not own. " * 4
    for entry in optional:
        project.ok(
            "finding", "decide", "--round", round_id, "--fingerprint",
            entry["fingerprint"], "--decision", "reject",
            "--reason", long_reason, "--reopen-if", "the generator is replaced",
        )  # fmt: skip
    project.ok("round", "close", "--round", round_id)
    assert len(entries) > 200

    context = context_of(project)

    assert len(context) < LIMIT
    lower = context.lower()
    blockers = lower.index("\nopen blockers (")
    assert blockers < lower.index("\nopen should-fix (")
    assert blockers < lower.index("\nregister of accepted risks (")
    assert "more line(s)" in lower  # the lists are cut, and say so
    assert "function 0 returns a constant" in context  # blockers come first
    assert "the generator is replaced" in context  # the register keeps a share
    # notes get only what is left once every blocker is listed
    assert "open notes (5):\n- ... and 5 more" in lower
    assert "last round: continue" in lower


def test_a_long_label_keeps_reason_and_reopen_condition(project):
    deep = "src/" + "/".join(["a_rather_deep_directory_name"] * 16)
    (project.root / deep).mkdir(parents=True)
    (project.root / deep / "mod.md").write_text("Some text.\n", encoding="utf-8")
    project.write_contract(
        "docs",
        'name = "docs"\ntemplate = "docs"\npaths = ["src/**"]\n\n[[criteria]]\n'
        'id = "LEN-1"\ntext = "Short"\ncheck = "pattern"\nrequired = true\n'
        'require = "^Missing$"\n',
    )
    round_id = project.ok("round", "start")["round"]
    project.ok("round", "check", "--round", round_id)
    [entry] = project.ok("finding", "list", "--round", round_id)["findings"]
    project.ok(
        "finding", "decide", "--round", round_id, "--fingerprint",
        entry["fingerprint"], "--decision", "accept", *REASON, *REOPEN,
    )  # fmt: skip
    project.ok("round", "close", "--round", round_id)
    assert len(entry["label"]) > 400

    context = context_of(project)

    assert REASON[1] in context and REOPEN[1] in context


def test_a_contract_edited_by_hand_is_flagged(project):
    project.add_artifact()
    project.ok("contract", "create", stdin=proposal(project))
    path = project.root / ".judicata" / "contracts" / "code.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Tests pass", "Tests pass now"),
        encoding="utf-8",
    )

    context = context_of(project)

    assert "edited by hand" in context.lower()
    assert "/judicata:contract" in context
