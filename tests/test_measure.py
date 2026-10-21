"""measure/measure.py: the maintainers' comparison of judicata with plain Claude.

Smoke test only (spec #5): the script runs on a tiny corpus with a fake `claude`
that answers from a reply queue, so no model and no tokens are involved.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "measure" / "measure.py"

CALC = '''"""Tiny calculator."""


def half(n):
    """Half of n, rounded down."""
    return n // 2


def ratio(a, b):
    """a divided by b, 0 when b is 0."""
    if b == 0:
        return 0
    return a / b
'''

CASE = {
    "contract": {
        "name": "code",
        "template": "code",
        "paths": ["src/**"],
        "reviewers": {"count": 1, "models": ["inherit"]},
        "stop": {"max_rounds": 5, "stall_rounds": 2},
        "criteria": [
            {
                "id": "CODE-1",
                "text": "No TODO or FIXME markers left in the code",
                "check": "pattern",
                "required": True,
                "forbid": "\\b(TODO|FIXME)\\b",
            }
        ],
    },
    "pick": 1,
    "mutations": [
        {
            "id": "half-third",
            "kind": "bug",
            "file": "src/calc.py",
            "find": "return n // 2",
            "replace": "return n // 3",
            "summary": "half() divides by 3",
        },
        {
            "id": "ratio-zero",
            "kind": "decoy",
            "file": "src/calc.py",
            "find": "return 0",
            "reason": "0 for b == 0 is intended, the report shows it as no data.",
            "reopen_if": "a caller must tell b == 0 apart from a real ratio of 0",
        },
    ],
}

FAKE = """
import json, os, sys, uuid
from pathlib import Path

if "--version" in sys.argv:
    print("9.9.9 (Fake Code)")
    sys.exit(0)
cwd = Path.cwd()
prompt = sys.stdin.read()
state = Path(os.environ["FAKE_STATE"])
state.mkdir(exist_ok=True)
counter = state / cwd.name
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
calc = cwd / "src" / "calc.py"
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({
        "dir": cwd.name, "argv": sys.argv[1:], "prompt": prompt,
        "calc": calc.read_text() if calc.exists() else None,
        "judicata": (cwd / ".judicata" / "contracts").is_dir(),
        "files": sorted(str(p.relative_to(cwd)) for p in cwd.rglob("*")
                        if ".git" not in p.parts and ".judicata" not in p.parts),
    }) + "\\n")
reply = json.loads(Path(os.environ["FAKE_REPLIES"]).read_text())[cwd.name][n]
session = str(uuid.uuid4())
text = reply.get("text", "Review finished.")
if "findings" in reply:
    text += "\\n\\n```findings\\n" + json.dumps(reply["findings"]) + "\\n```\\n"
print(json.dumps({"type": "system", "subtype": "init", "session_id": session,
                  "model": "fake-model"}))
if reply.get("skill"):
    print(json.dumps({"type": "assistant", "session_id": session, "message": {
        "content": [{"type": "tool_use", "name": "Skill",
                     "input": {"skill": "judicata:review"}}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": text, "session_id": session, "num_turns": 3,
                  "total_cost_usd": reply.get("cost", 0.25)}))
"""


def blocker(file, line, summary="a problem"):
    return {"file": file, "line": line, "severity": "blocker", "summary": summary}


ROUND_1 = {
    "done": False,
    "findings": [
        blocker("src/calc.py", 6, "half() divides by 3"),
        blocker("src/calc.py", 11, "ratio() hides b == 0"),
    ],
}

REPLIES = {
    "seeded-tiny-judicata-t1": [
        {"findings": ROUND_1, "skill": True},
        {"text": "Recorded your decision."},
        {"findings": {"done": True, "findings": []}, "skill": True},
    ],
    "seeded-tiny-plain-t1": [
        {"findings": ROUND_1},
        {"text": "Understood."},
        {
            "findings": {
                "done": False,
                "findings": [blocker("src/calc.py", 11, "ratio() returns 0")],
            }
        },
    ],
    "eval-01-rejected-finding-judicata-t1": [
        {"findings": {"done": True, "findings": []}, "skill": True},
    ],
    "eval-01-rejected-finding-plain-t1": [
        {
            "findings": {
                "done": False,
                "findings": [blocker("src/stats.py", 7, "mean([]) returns 0.0")],
            }
        },
    ],
}


def setup(tmp_path: Path, replies: dict = REPLIES) -> dict:
    corpus = tmp_path / "seeded" / "tiny"
    (corpus / "project" / "src").mkdir(parents=True)
    (corpus / "project" / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    (corpus / "case.json").write_text(json.dumps(CASE), encoding="utf-8")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\n{textwrap.dedent(FAKE)}", encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "replies.json").write_text(json.dumps(replies), encoding="utf-8")
    return {
        **os.environ,
        "FAKE_LOG": str(tmp_path / "calls.jsonl"),
        "FAKE_STATE": str(tmp_path / "fake-state"),
        "FAKE_REPLIES": str(tmp_path / "replies.json"),
        "JUDICATA_STATE_DIR": str(tmp_path / "state"),
    }


def measure(tmp_path: Path, env: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--claude",
            str(tmp_path / "bin" / "claude"),
            "--seeded-dir",
            str(tmp_path / "seeded"),
            "--case",
            "eval/01-*",
            "--case",
            "seeded/*",
            "--max-rounds",
            "2",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--summary",
            str(tmp_path / "measurement.md"),
            "--work-dir",
            str(tmp_path / "work"),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )


def calls(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_compares_both_arms_and_writes_the_summary(tmp_path):
    env = setup(tmp_path)
    proc = measure(tmp_path, env, "--max-cost-usd", "10", "--max-run-usd", "1.5")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    log = calls(tmp_path)
    assert len(log) == 8
    for call in log:
        argv = call["argv"]
        assert "-p" in argv
        assert argv[argv.index("--max-budget-usd") + 1] == "1.5"
        # shell commands of both arms run in Claude Code's sandbox
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"]["allowUnsandboxedCommands"] is False
        assert "Bash" not in argv  # every shell command only with --allow-bash
        judicata = "-judicata-" in call["dir"]
        assert ("--plugin-dir" in argv) is judicata
        assert call["judicata"] is judicata
        # the truth about seeded bugs never lands in the project
        assert not any("case.json" in f or "setup.json" in f for f in call["files"])

    seeded = [c for c in log if c["dir"] == "seeded-tiny-judicata-t1"]
    first, decision, second = seeded
    assert "return n // 3" in first["calc"]
    assert "--resume" not in first["argv"]
    # decisions go to the same session, the next review starts fresh
    assert "--resume" in decision["argv"]
    assert "src/calc.py" in decision["prompt"] and "11" in decision["prompt"]
    assert CASE["mutations"][1]["reason"] in decision["prompt"]
    assert "half() divides by 3" not in decision["prompt"]
    assert "--resume" not in second["argv"]
    # the reported seeded bug was fixed before the next round
    assert "return n // 2" in second["calc"]

    summary = (tmp_path / "measurement.md").read_text(encoding="utf-8")
    assert "fake-model" in summary
    assert "9.9.9" in summary
    assert "cases: 2" in summary
    rows = {
        line.split("|")[1].strip(): [cell.strip() for cell in line.split("|")[2:4]]
        for line in summary.splitlines()
        if line.startswith("| ") and not line.startswith("| Metric")
    }
    # plain Claude never heard of the scaffold's rejection, only of its own
    assert rows["Re-raise rate"] == ["0 of 2 (0 %)", "1 of 1 (100 %)"]
    assert rows["Converged within 2 rounds"] == ["1 of 1, mean 2.0 rounds", "0 of 1"]
    assert rows["Blocker precision"] == ["1 of 2 (50 %)", "1 of 3 (33 %)"]
    assert rows["Blocker recall"] == ["1 of 1 (100 %)", "1 of 1 (100 %)"]
    assert rows["Skill fired"] == ["3 of 3 sessions", "n/a"]
    assert str(tmp_path) not in summary

    runs = list((tmp_path / "raw").iterdir())
    assert len(runs) == 1
    results = json.loads((runs[0] / "results.json").read_text(encoding="utf-8"))
    assert len(results["trials"]) == 4
    assert len(list((runs[0] / "sessions").iterdir())) == 8


def test_stops_at_the_cost_cap(tmp_path):
    env = setup(tmp_path)
    proc = measure(tmp_path, env, "--max-cost-usd", "0.3", "--max-run-usd", "1")
    assert proc.returncode == 2, proc.stdout + proc.stderr

    log = calls(tmp_path)
    assert len(log) == 2
    budgets = [c["argv"][c["argv"].index("--max-budget-usd") + 1] for c in log]
    assert budgets == ["0.3", "0.05"]
    summary = (tmp_path / "measurement.md").read_text(encoding="utf-8")
    assert "stopped at the cost cap" in summary


def test_raw_results_never_go_into_the_repository(tmp_path):
    env = setup(tmp_path)
    proc = measure(tmp_path, env, "--raw-dir", str(REPO / "measure" / "raw"))
    assert proc.returncode == 1
    assert "outside" in proc.stderr
    assert not (tmp_path / "calls.jsonl").exists()


TWO = '''"""Tiny calculator."""


def half(n):
    """Half of n."""
    return n // 2


def double(n):
    """Twice n."""
    return n * 2
'''


def test_counts_each_finding_once_and_leaves_out_broken_replies(tmp_path):
    env = setup(
        tmp_path,
        {
            # one blocker between both seeded bugs is about the closer one only
            "seeded-tiny-plain-t1": [
                {"findings": {"done": False, "findings": [blocker("src/calc.py", 9)]}}
            ],
            # done although both seeded bugs are still there
            "seeded-tiny-plain-t2": [{"findings": {"done": True, "findings": []}}],
            # no findings block, or a broken one: no measurement
            "eval-01-rejected-finding-plain-t1": [{"text": "Looks fine."}],
            "eval-01-rejected-finding-plain-t2": [
                {"findings": {"done": True, "findings": [42]}}
            ],
        },
    )
    case = tmp_path / "seeded" / "tiny"
    (case / "project" / "src" / "calc.py").write_text(TWO, encoding="utf-8")
    spec = {
        "contract": CASE["contract"],
        "pick": 2,
        "mutations": [
            {
                "id": "half",
                "kind": "bug",
                "file": "src/calc.py",
                "find": "return n // 2",
                "replace": "return n // 3",
            },
            {
                "id": "double",
                "kind": "bug",
                "file": "src/calc.py",
                "find": "return n * 2",
                "replace": "return n * 3",
            },
        ],
    }
    (case / "case.json").write_text(json.dumps(spec), encoding="utf-8")
    proc = measure(
        tmp_path, env, "--arm", "plain", "--trials", "2", "--max-rounds", "1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    summary = (tmp_path / "measurement.md").read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip(): line.split("|")[3].strip()
        for line in summary.splitlines()
        if line.startswith("| ")
    }
    assert rows["Blocker precision"] == "1 of 1 (100 %)"
    assert rows["Blocker recall"] == "1 of 4 (25 %)"
    assert rows["Converged within 1 rounds"] == "1 of 2, mean 1.0 rounds"
    assert rows["Said done with seeded bugs left"] == "1 of 1"
    assert rows["Trials left out (failed or no block)"] == "2 of 4"
    assert rows["Done as expected (eval cases)"] == "n/a"


def test_leaves_out_eval_cases_whose_truth_says_so(tmp_path):
    evals = tmp_path / "evals"
    for name in ("01-review", "02-edit"):
        (evals / name).mkdir(parents=True)
        (evals / name / "case.yaml").write_text(
            "execution:\n  prompt: |\n    Review src please.\n", encoding="utf-8"
        )
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "01-review": {"done": True, "bugs": [], "decided": []},
                "02-edit": {"measure": False, "why": "the prompt asks for an edit"},
            }
        ),
        encoding="utf-8",
    )
    common = [
        sys.executable,
        str(SCRIPT),
        "--evals-dir",
        str(evals),
        "--seeded-dir",
        str(tmp_path / "none"),
        "--truth",
        str(truth),
        "--raw-dir",
        str(tmp_path / "raw"),
        "--dry-run",
    ]
    (tmp_path / "none").mkdir()
    proc = subprocess.run(
        [*common, "--case", "eval/*"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "eval/01-review" in proc.stdout
    assert "eval/02-edit" not in proc.stdout
    alone = subprocess.run(
        [*common, "--case", "eval/02-*"], capture_output=True, text=True, check=False
    )
    assert alone.returncode == 1
    assert "no case matches" in alone.stderr
