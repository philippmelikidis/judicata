"""Prepare the throwaway project of one eval case (claude plugin eval --scaffold).

Runs in the run's working directory, called by <case>/scaffold.sh with the case
directory as its only argument. It copies <case>/project/ into the working
directory, commits it to a new Git repository, stores the contract from
<case>/setup.json through the core and plays the rounds listed there, so the
case starts with a real ledger, written by the core and not by hand:

    {
      "contract": {... contract create input ...},
      "rounds": [
        {
          "reviewer": {... one reviewer answer for every planned run ...},
          "decide": [
            {"category": "correctness", "decision": "reject",
             "reason": "...", "reopen_if": "..."}
          ]
        }
      ]
    }

A round without "decide" leaves its findings open. "for": [paths] starts the
round for those paths only (round start --for), "close": false leaves it open
after the reviewer answers and decisions, as a session that ended in the
middle of a review would. The ledger is committed at the end, as a user would
after a review.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "scripts" / "judicata.py"


def core(project: Path, *args: str, stdin: object = None) -> dict:
    data = b"" if stdin is None else json.dumps(stdin).encode("utf-8")
    proc = subprocess.run(
        [sys.executable, str(CORE), *args, "--project", str(project)],
        input=data,
        capture_output=True,
        check=False,
    )
    try:
        answer = json.loads(proc.stdout)
    except json.JSONDecodeError:
        answer = None
    if proc.returncode != 0 or not answer or answer.get("ok") is not True:
        sys.exit(
            f"scaffold: judicata {' '.join(args)} failed:\n"
            f"{proc.stdout.decode(errors='replace')}{proc.stderr.decode(errors='replace')}"
        )
    return answer


def git(project: Path, *args: str) -> None:
    # an identity of its own, also outside the eval's sandbox home
    identity = [
        "-c",
        "user.name=judicata eval",
        "-c",
        "user.email=eval@example.invalid",
    ]
    subprocess.run(
        ["git", *identity, *args], cwd=project, check=True, capture_output=True
    )


def play_round(project: Path, spec: dict) -> None:
    start = [arg for path in spec.get("for", []) for arg in ("--for", path)]
    round_id = core(project, "round", "start", *start)["round"]
    core(project, "round", "check", "--round", round_id)
    plan = core(project, "review", "plan", "--round", round_id)
    for run in plan["runs"]:
        core(
            project,
            "review",
            "submit",
            "--round",
            round_id,
            "--run",
            run["run"],
            stdin=spec.get("reviewer", {"criteria": [], "findings": []}),
        )
    listed = core(project, "finding", "list", "--round", round_id)["findings"]
    for wanted in spec.get("decide", []):
        matches = [f for f in listed if f["category"] == wanted["category"]]
        if len(matches) != 1:
            sys.exit(f"scaffold: expected one {wanted['category']} finding: {listed}")
        args = [
            "finding",
            "decide",
            "--round",
            round_id,
            "--fingerprint",
            matches[0]["fingerprint"],
            "--decision",
            wanted["decision"],
        ]
        if "reason" in wanted:
            args += ["--reason", wanted["reason"], "--reopen-if", wanted["reopen_if"]]
        core(project, *args)
    if spec.get("close", True):
        core(project, "round", "close", "--round", round_id)


def main() -> None:
    case = Path(sys.argv[1]).resolve()
    project = Path.cwd()
    setup = json.loads((case / "setup.json").read_text(encoding="utf-8"))
    shutil.copytree(case / "project", project, dirs_exist_ok=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "Artifact")
    core(project, "contract", "create", stdin=setup["contract"])
    for spec in setup.get("rounds", []):
        play_round(project, spec)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "judicata contract and ledger")


if __name__ == "__main__":
    main()
