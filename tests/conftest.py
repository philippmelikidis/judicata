"""Test helpers: every test drives the core command line on a throwaway project."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "judicata.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Project:
    def __init__(self, root: Path, state_dir: Path):
        self.root = root
        # Local approvals of this user. Point it elsewhere to play another user
        # or machine that got the same repository.
        self.state_dir = state_dir
        self.env: dict[str, str | None] = {}

    def run(self, *args: str, stdin=None) -> tuple[int, dict]:
        """Run the core and return (exit code, parsed JSON from stdout).

        stdin: a dict or list is sent as JSON, str and bytes as they are.
        The core keeps local approvals in self.state_dir (JUDICATA_STATE_DIR);
        self.env adds variables, None removes one.
        """
        if stdin is None:
            stdin = b""
        elif isinstance(stdin, str):
            stdin = stdin.encode("utf-8")
        elif not isinstance(stdin, bytes):
            stdin = json.dumps(stdin).encode("utf-8")
        env = {**os.environ, "JUDICATA_STATE_DIR": str(self.state_dir)}
        for key, value in self.env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=False,
            capture_output=True,
            timeout=60,
            input=stdin,
            env=env,
        )
        stdout = proc.stdout.decode("utf-8")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise AssertionError(
                f"stdout is not JSON (exit {proc.returncode}):\n"
                f"stdout: {stdout!r}\nstderr: {proc.stderr!r}"
            ) from None
        return proc.returncode, payload

    def hook(self, event: str = "session-start", stdin=None, cwd=None):
        """Run the core as a hook does: (exit code, stdout text, stderr text).

        stdin: the hook input (dict as JSON, str or bytes); by default the
        SessionStart input of a session started in this project (or in cwd).
        """
        if stdin is None:
            stdin = {
                "session_id": "test",
                "hook_event_name": "SessionStart",
                "source": "startup",
                "cwd": str(cwd or self.root),
            }
        if isinstance(stdin, str):
            stdin = stdin.encode("utf-8")
        elif not isinstance(stdin, bytes):
            stdin = json.dumps(stdin).encode("utf-8")
        env = {**os.environ, "JUDICATA_STATE_DIR": str(self.state_dir)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", event],
            check=False,
            capture_output=True,
            timeout=60,
            input=stdin,
            env=env,
            cwd=str(cwd or self.root),
        )
        return (
            proc.returncode,
            proc.stdout.decode("utf-8"),
            proc.stderr.decode("utf-8"),
        )

    def core(self, *args: str, stdin=None) -> tuple[int, dict]:
        """Run a subcommand against this project (adds --project)."""
        return self.run(*args, "--project", str(self.root), stdin=stdin)

    def ok(self, *args: str, stdin=None) -> dict:
        code, payload = self.core(*args, stdin=stdin)
        assert code == 0, payload
        assert payload["ok"] is True, payload
        return payload

    def fail(self, *args: str, stdin=None) -> dict:
        code, payload = self.core(*args, stdin=stdin)
        assert code != 0, payload
        assert payload["ok"] is False, payload
        return payload["error"]

    def write_contract(self, name: str, text: str, *, approve: bool = True) -> Path:
        """Write a contract file by hand.

        approve: approve its commands locally, as if this user wrote it. Pass
        False to get a contract that came in with the repository. Invalid
        contracts are written but cannot be approved.
        """
        path = self.root / ".judicata" / "contracts" / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if approve:
            code, shown = self.core("contract", "show", "--contract", name)
            if code == 0:
                self.approve(name, shown["approval"]["commands_hash"])
        return path

    def approve(self, name: str, commands_hash: str) -> dict:
        return self.ok(
            "contract", "approve", "--contract", name, "--commands-hash", commands_hash
        )

    def review(self, *start_args: str) -> dict:
        """One full round: start, deterministic checks, close."""
        round_id = self.ok("round", "start", *start_args)["round"]
        self.ok("round", "check", "--round", round_id)
        return self.ok("round", "close", "--round", round_id)

    def add_artifact(self) -> None:
        """Copy the sample artifact (src/app.py) the reviewer fixtures refer to."""
        shutil.copytree(FIXTURES / "artifact", self.root, dirs_exist_ok=True)

    def judged_round(self, *plan_args: str) -> tuple[str, list[dict]]:
        """Start and check a round, plan the reviewers: (round id, runs)."""
        round_id = self.ok("round", "start")["round"]
        self.ok("round", "check", "--round", round_id)
        plan = self.ok("review", "plan", "--round", round_id, *plan_args)
        return round_id, plan["runs"]

    def submit(self, round_id: str, run: dict, output: str) -> tuple[int, dict]:
        """Hand one reviewer output (JSON text) to the core via stdin."""
        return self.core(
            "review", "submit", "--round", round_id, "--run", run["run"], stdin=output
        )

    @property
    def ledger(self) -> Path:
        return self.root / ".judicata" / "ledger.jsonl"

    def ledger_events(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "project"
    root.mkdir()
    return Project(root, tmp_path / "state")


def command_contract(
    command: str, *, required: bool = True, timeout: int | None = None
) -> str:
    """A contract with a single command criterion."""
    lines = [
        'name = "code"',
        'template = "code"',
        'paths = ["**"]',
        "",
        "[[criteria]]",
        'id = "CODE-1"',
        'text = "Tests pass"',
        'check = "command"',
        f"required = {'true' if required else 'false'}",
        f"command = {json.dumps(command)}",
    ]
    if timeout is not None:
        lines.append(f"timeout = {timeout}")
    return "\n".join(lines) + "\n"


def reviewer_output(name: str) -> str:
    """A reviewer output fixture from tests/fixtures/reviewer/<name>.json."""
    return (FIXTURES / "reviewer" / f"{name}.json").read_text(encoding="utf-8")


def judgment_contract(*, required: bool = True, reviewers: str = "") -> str:
    """A contract with one command criterion and one judgment criterion JUD-1."""
    text = command_contract("true") + (
        "\n[[criteria]]\n"
        'id = "JUD-1"\n'
        'text = "No known correctness bugs in scope"\n'
        'check = "judgment"\n'
        f"required = {'true' if required else 'false'}\n"
    )
    if reviewers:
        text = text.replace(
            'paths = ["**"]', f'paths = ["**"]\n\n[reviewers]\n{reviewers}'
        )
    return text


def proposal(project, template: str = "code", **changes) -> dict:
    """The template's proposal, filled the way the interview would fill it."""
    shown = project.ok("template", "show", "--template", template)
    draft = shown["proposal"]
    draft["paths"] = ["**"]
    for criterion in draft["criteria"]:
        if criterion["check"] == "command":
            criterion["command"] = "true"
    draft.update(changes)
    return draft
