#!/usr/bin/env python3
"""Compare judicata with plain Claude on a corpus with known truth (maintainers only).

Every case is played in two arms: Claude Code with this plugin (--plugin-dir) and
the same Claude Code without it. Each arm gets the same throwaway project and the
same prompts; the plain arm gets the artifact without `.judicata/`. The corpus:

- eval cases (`evals/<case>/`, `eval/<case>`): the artifact and, for judicata,
  the contract and earlier rounds played by `evals/_lib/scaffold.py`. Truth in
  `measure/evals.json`. One fresh session per trial.
- seeded cases (`measure/seeded/<case>/`, `seeded/<case>`): a clean project plus
  mutations from `case.json`. Each trial applies a pick of the bug mutations
  (seeded random) and the decoys, then plays rounds: a fresh review session, the
  user's decisions in the same session (every point that is not a seeded bug is
  rejected), the reported seeded bugs fixed, and the next round in a fresh session,
  until a review says done or `--max-rounds` is reached.

Every review prompt asks for a `findings` block (JSON) at the end of the reply,
the same for both arms; metrics come from these blocks only:

- re-raise rate: points decided earlier (in the scaffold or in an earlier session)
  that a fresh session reports as open again,
- convergence: seeded trials whose review said done (no blocker) within the limit,
- blocker precision and recall of the first review against the seeded bugs.

Cost cap: every `claude -p` call gets `--max-budget-usd` (the smaller of
--max-run-usd and what is left of --max-cost-usd); no call starts once the total
reached --max-cost-usd (exit 2, the summary says so). Raw results (streams, replies,
per trial details) go to --raw-dir, which must lie outside this repository; the
summary goes into one document (default docs/measurement.md), newest first.
Not shipped, not run in CI. Runs Claude as you on throwaway projects: only on
corpora you wrote.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAFFOLD = REPO / "evals" / "_lib" / "scaffold.py"
MARKER = "<!-- measurement results, newest first -->"
ARMS = ("judicata", "plain")
WINDOW = 3
SEVERITIES = ("blocker", "should-fix", "note")
ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Agent",
    "Skill",
    "Bash(python3 *)",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git log*)",
    "Bash(ls*)",
]
DISALLOWED_TOOLS = ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
# Shell commands run in Claude Code's sandbox (bubblewrap on Linux, needs bwrap and
# socat): writes only below the project, no network. The sandbox approves simple
# commands; in dontAsk mode commands it cannot analyse (a heredoc, a command name
# from a variable) are denied unless --allow-bash.
SANDBOX = {
    "sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
}

FORMAT = """
At the very end of your reply, add one fenced code block with the info string
`findings` that holds JSON: {"done": true or false, "findings": [{"file": "path
relative to the project", "line": number or null, "severity": "blocker" or
"should-fix" or "note", "summary": "one sentence"}]}. List every problem you
report as open in this reply; leave out points that were decided earlier and that
you do not raise again. "done" is true only if nothing blocks the work."""

SEEDED_PROMPT = (
    "Review the code in src/ please: is it done? I can't answer follow-up "
    "questions right now, so leave anything that needs my decision open. Do not "
    "edit the code."
)
AGAIN_PROMPT = (
    "I fixed some of the points since the last review. Review the code in src/ "
    "again please: is it done now? I can't answer follow-up questions right now, "
    "so leave anything that needs my decision open. Do not edit the code."
)
GENERIC_REASON = "This is intended, not a problem."
GENERIC_REOPEN = "a user reports this behavior as a bug"


class Usage(Exception):
    """A problem with the command line or the corpus: exit 1 with the message."""


# ---------------------------------------------------------------- corpus


@dataclass
class Item:
    """A point with known truth: a seeded bug or a decided (decoy) point."""

    id: str
    file: str
    line: int | None
    keywords: list[str] = field(default_factory=list)
    reason: str = GENERIC_REASON
    reopen_if: str = GENERIC_REOPEN

    def matches(self, finding: dict) -> bool:
        if _path(finding.get("file")) != _path(self.file):
            return False
        line = finding.get("line")
        if isinstance(line, int) and self.line is not None:
            return abs(line - self.line) <= WINDOW
        if line is None and self.line is None and not self.keywords:
            return True  # a point about the whole file
        summary = str(finding.get("summary", "")).casefold()
        return any(word.casefold() in summary for word in self.keywords)


@dataclass
class Case:
    name: str  # "eval/<dir>" or "seeded/<dir>"
    kind: str
    path: Path
    prompt: str
    expected_done: bool | None = None
    bugs: list[Item] = field(default_factory=list)
    decided: list[Item] = field(default_factory=list)
    spec: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.name.replace("/", "-")


def _path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _items(raw: list[dict]) -> list[Item]:
    return [
        Item(
            id=entry["id"],
            file=entry["file"],
            line=entry.get("line"),
            keywords=entry.get("keywords", []),
            reason=entry.get("reason", GENERIC_REASON),
            reopen_if=entry.get("reopen_if", GENERIC_REOPEN),
        )
        for entry in raw
    ]


def _yaml_prompt(text: str) -> str:
    """The `prompt: |` block of an eval case.yaml (no YAML library needed)."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "prompt: |":
            indent = len(line) - len(line.lstrip()) + 1
            block = []
            for nxt in lines[index + 1 :]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) < indent:
                    break
                block.append(nxt.strip())
            return " ".join(part for part in block if part)
    raise Usage("case.yaml without a 'prompt: |' block")


def load_cases(args: argparse.Namespace) -> list[Case]:
    cases: list[Case] = []
    truth_path = Path(args.truth)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    evals = Path(args.evals_dir)
    for case_dir in sorted(p for p in evals.iterdir() if (p / "case.yaml").is_file()):
        name = f"eval/{case_dir.name}"
        if case_dir.name not in truth:
            raise Usage(f"{name}: no truth in {truth_path.name}")
        entry = truth[case_dir.name]
        cases.append(
            Case(
                name=name,
                kind="eval",
                path=case_dir,
                prompt=_yaml_prompt((case_dir / "case.yaml").read_text("utf-8")),
                expected_done=entry.get("done"),
                bugs=_items(entry.get("bugs", [])),
                decided=_items(entry.get("decided", [])),
            )
        )
    seeded = Path(args.seeded_dir)
    for case_dir in sorted(p for p in seeded.iterdir() if (p / "case.json").is_file()):
        spec = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        _check_mutations(case_dir, spec)
        cases.append(
            Case(
                name=f"seeded/{case_dir.name}",
                kind="seeded",
                path=case_dir,
                prompt=spec.get("prompt", SEEDED_PROMPT),
                spec=spec,
            )
        )
    wanted = args.case or ["*"]
    chosen = [c for c in cases if any(fnmatch.fnmatch(c.name, g) for g in wanted)]
    if not chosen:
        raise Usage(f"no case matches {wanted}")
    return chosen


def _check_mutations(case_dir: Path, spec: dict) -> None:
    for mutation in spec.get("mutations", []):
        source = case_dir / "project" / mutation["file"]
        text = source.read_text(encoding="utf-8")
        if text.count(mutation["find"]) != 1:
            raise Usage(
                f"{case_dir.name}/{mutation['id']}: 'find' must occur exactly once"
            )
        replace = mutation.get("replace", mutation["find"])
        if replace.count("\n") != mutation["find"].count("\n"):
            raise Usage(f"{case_dir.name}/{mutation['id']}: replace changes lines")


# ---------------------------------------------------------------- projects


def _git(project: Path, *args: str) -> None:
    identity = [
        "-c",
        "user.name=judicata measure",
        "-c",
        "user.email=m@example.invalid",
    ]
    subprocess.run(
        ["git", *identity, *args], cwd=project, check=True, capture_output=True
    )


def _commit(project: Path, message: str) -> None:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "--allow-empty", "-m", message)


def _plain_project(source: Path, project: Path) -> None:
    shutil.copytree(source, project, dirs_exist_ok=True)
    _git(project, "init", "-q", "-b", "main")
    _commit(project, "Artifact")


def _scaffold(case_dir: Path, project: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCAFFOLD), str(case_dir)],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scaffold failed: {proc.stdout}{proc.stderr}")


def seed(case: Case, trial: int, seed_text: str, staging: Path) -> tuple[Path, list]:
    """Mutated copy of a seeded case as an eval-like case folder.

    Returns the folder (project/ and setup.json) and the applied bug mutations
    with their line. The folder lies in the work dir, never in the project.
    """
    spec = case.spec
    folder = staging / f"{case.slug}-t{trial}"
    shutil.copytree(case.path / "project", folder / "project")
    bugs = [m for m in spec["mutations"] if m["kind"] == "bug"]
    pick = min(spec.get("pick", len(bugs)), len(bugs))
    chosen = random.Random(f"{seed_text}/{case.name}/{trial}").sample(bugs, pick)
    applied = []
    for mutation in spec["mutations"]:
        if mutation["kind"] == "bug" and mutation not in chosen:
            continue
        target = folder / "project" / mutation["file"]
        text = target.read_text(encoding="utf-8")
        start = text.index(mutation["find"])
        line = text.count("\n", 0, start) + 1
        replace = mutation.get("replace", mutation["find"])
        target.write_text(text.replace(mutation["find"], replace), encoding="utf-8")
        applied.append({**mutation, "line": line})
    setup = {"contract": spec["contract"], "rounds": []}
    (folder / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
    return folder, applied


def _fix(project: Path, mutation: dict) -> None:
    target = project / mutation["file"]
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace(mutation["replace"], mutation["find"], 1), encoding="utf-8"
    )


# ---------------------------------------------------------------- runner


class CostCap(Exception):
    """The total cost cap is reached: no further call starts."""


@dataclass
class Runner:
    claude: str
    plugin_dir: Path
    model: str | None
    max_run: float
    max_total: float
    timeout: int
    sessions_dir: Path
    allow_bash: bool = False
    spent: float = 0.0
    calls: int = 0
    models: set = field(default_factory=set)

    def run(
        self, project: Path, arm: str, prompt: str, label: str, resume: str | None
    ) -> dict:
        left = self.max_total - self.spent
        if left <= 1e-9:
            raise CostCap
        budget = min(self.max_run, left)
        cmd = [self.claude, "-p", "--output-format", "stream-json", "--verbose"]
        cmd += ["--setting-sources", "project,local", "--strict-mcp-config"]
        cmd += ["--permission-mode", "dontAsk", "--max-budget-usd", _money(budget)]
        # opt-in: every shell command, still inside the sandbox (like
        # claude plugin eval --allow-tools Bash); otherwise only the rules above
        cmd += [
            "--allowedTools",
            *ALLOWED_TOOLS,
            *(["Bash"] if self.allow_bash else []),
        ]
        cmd += ["--disallowedTools", *DISALLOWED_TOOLS]
        cmd += ["--settings", json.dumps(SANDBOX)]
        if self.model:
            cmd += ["--model", self.model]
        if arm == "judicata":
            cmd += ["--plugin-dir", str(self.plugin_dir)]
        if resume:
            cmd += ["--resume", resume]
        self.calls += 1
        try:
            proc = subprocess.run(
                cmd,
                cwd=project,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            stream, error = proc.stdout, proc.stderr[-2000:]
        except subprocess.TimeoutExpired as expired:
            stream = (expired.stdout or b"").decode("utf-8", "replace")
            error = "timeout"
        (self.sessions_dir / f"{label}.jsonl").write_text(stream, encoding="utf-8")
        session = _parse_stream(stream)
        # a call without a reported cost counts with its whole budget
        cost = session["cost"] if session["cost"] is not None else budget
        self.spent += cost
        if session["model"]:
            self.models.add(session["model"])
        session.update(label=label, cost=cost, budget=budget, error=error or None)
        return session

    def version(self) -> str:
        proc = subprocess.run(
            [self.claude, "--version"], capture_output=True, text=True, check=False
        )
        return proc.stdout.strip().split(" ")[0] or "unknown"


def _money(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _parse_stream(stream: str) -> dict:
    out = {"session_id": None, "model": None, "result": None, "cost": None}
    out.update(skill=False, subtype=None, turns=None, is_error=None)
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["model"] = event.get("model")
            out["session_id"] = event.get("session_id")
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                    and "judicata" in json.dumps(block.get("input"))
                ):
                    out["skill"] = True
        elif kind == "result":
            out["result"] = event.get("result")
            out["cost"] = event.get("total_cost_usd")
            out["subtype"] = event.get("subtype")
            out["turns"] = event.get("num_turns")
            out["is_error"] = event.get("is_error")
            out["session_id"] = event.get("session_id") or out["session_id"]
    return out


FENCE = re.compile(r"```findings[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)


def parse_findings(text: str | None) -> dict | None:
    """The last `findings` block of a reply, or None when it is missing or broken."""
    blocks = FENCE.findall(text or "")
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return None
    findings = []
    for entry in data["findings"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            return None  # a broken entry makes the whole block unusable
        severity = str(entry.get("severity", "note")).casefold()
        findings.append(
            {
                "file": _path(entry.get("file")),
                "line": entry.get("line")
                if isinstance(entry.get("line"), int)
                else None,
                "severity": severity if severity in SEVERITIES else "note",
                "summary": str(entry.get("summary", ""))[:500],
            }
        )
    return {"done": data.get("done") is True, "findings": findings}


# ---------------------------------------------------------------- trials


def _review(runner: Runner, project: Path, arm: str, prompt: str, label: str) -> dict:
    session = runner.run(project, arm, prompt + "\n" + FORMAT, label, None)
    parsed = parse_findings(session["result"])
    session["parsed"] = parsed is not None
    session["done"] = bool(parsed and parsed["done"])
    session["findings"] = parsed["findings"] if parsed else []
    # a failed, cut or unparsable review is no measurement: its trial is left out
    session["ok"] = parsed is not None and _finished(session)
    return session


def _finished(session: dict) -> bool:
    return session["subtype"] == "success" and not session["is_error"]


def _assign(finding: dict, items: list[Item]) -> Item | None:
    """The one known point a finding is about: the closest matching line."""
    matching = [item for item in items if item.matches(finding)]
    if not matching:
        return None
    line = finding.get("line")
    if not isinstance(line, int):
        return matching[0]
    return min(matching, key=lambda item: abs((item.line or line) - line))


def _hits(findings: list[dict], items: list[Item], among: list[Item]) -> set[str]:
    """IDs of items that findings are about, each finding counted once."""
    assigned = (_assign(f, among) for f in findings)
    ids = {item.id for item in items}
    return {item.id for item in assigned if item and item.id in ids}


def _score_first(trial: dict, session: dict, bugs: list[Item]) -> None:
    blockers = [f for f in session["findings"] if f["severity"] == "blocker"]
    trial["blockers"] = len(blockers)
    trial["true_blockers"] = sum(_assign(f, bugs) is not None for f in blockers)
    trial["bugs"] = len(bugs)
    trial["bugs_hit_blocker"] = len(_hits(blockers, bugs, bugs))
    trial["bugs_hit_any"] = len(_hits(session["findings"], bugs, bugs))


def _reraised(session: dict, decided: list[Item], bugs: list[Item]) -> list[str]:
    return sorted(_hits(session["findings"], decided, bugs + decided))


def eval_trial(runner: Runner, case: Case, arm: str, n: int, work: Path) -> dict:
    project = work / f"{case.slug}-{arm}-t{n}"
    project.mkdir()
    if arm == "judicata":
        _scaffold(case.path, project)
    else:
        _plain_project(case.path / "project", project)
    trial = {"case": case.name, "kind": "eval", "arm": arm, "trial": n}
    trial["sessions"] = []
    session = _review(runner, project, arm, case.prompt, f"{project.name}-r1")
    trial["sessions"].append(_public(session))
    _score_first(trial, session, case.bugs)
    # the plain arm never heard of the scaffold's decisions: nothing to re-raise
    decided = case.decided if arm == "judicata" else []
    trial["decided"] = len(decided)
    trial["reraised"] = _reraised(session, decided, case.bugs)
    trial["valid"] = session["ok"]
    trial["expected_done"] = case.expected_done
    trial["done"] = session["done"]
    return trial


def seeded_trial(
    runner: Runner, case: Case, arm: str, n: int, work: Path, args
) -> dict:
    folder, applied = seed(case, n, args.seed, work / "_staging" / arm)
    project = work / f"{case.slug}-{arm}-t{n}"
    project.mkdir()
    if arm == "judicata":
        _scaffold(folder, project)
    else:
        _plain_project(folder / "project", project)
    bugs = [
        Item(m["id"], m["file"], m["line"], m.get("keywords", []))
        for m in applied
        if m["kind"] == "bug"
    ]
    decoys = {
        m["id"]: Item(
            m["id"],
            m["file"],
            m["line"],
            m.get("keywords", []),
            m.get("reason", GENERIC_REASON),
            m.get("reopen_if", GENERIC_REOPEN),
        )
        for m in applied
        if m["kind"] == "decoy"
    }
    trial = {"case": case.name, "kind": "seeded", "arm": arm, "trial": n}
    trial.update(seeded=[m["id"] for m in applied], sessions=[], decided=0)
    trial.update(reraised=[], converged_round=None, rounds=0, valid=True)
    decided: list[Item] = []
    fixed: set[str] = set()
    for rnd in range(1, args.max_rounds + 1):
        prompt = case.prompt if rnd == 1 else AGAIN_PROMPT
        label = f"{project.name}-r{rnd}"
        session = _review(runner, project, arm, prompt, label)
        trial["sessions"].append(_public(session))
        trial["rounds"] = rnd
        if not session["ok"]:
            trial["valid"] = False
            break
        if rnd == 1:
            _score_first(trial, session, bugs)
        else:
            trial["decided"] += len(decided)
            trial["reraised"] += _reraised(session, decided, bugs)
        blockers = [f for f in session["findings"] if f["severity"] == "blocker"]
        if session["done"] and not blockers:
            trial["converged_round"] = rnd
            trial["bugs_left_at_done"] = len(bugs) - len(fixed)
            break
        if rnd == args.max_rounds:
            break
        # the user: reject what is not a seeded bug, fix the seeded bugs reported
        rejects = []
        known = bugs + decided + list(decoys.values())
        for finding in session["findings"]:
            point = _assign(finding, known + [r for r, _ in rejects])
            if point in bugs or point in decided or point in [r for r, _ in rejects]:
                continue
            item = point or Item(
                f"point-{len(decided) + len(rejects) + 1}",
                finding["file"],
                finding["line"],
            )
            rejects.append((item, finding))
        if rejects and session["session_id"]:
            text = _decisions(rejects)
            label = f"{project.name}-r{rnd}-decisions"
            turn = runner.run(project, arm, text, label, session["session_id"])
            trial["sessions"].append(_public(turn))
            # only a decision the session received counts as decided
            if _finished(turn):
                decided += [item for item, _ in rejects]
        reported = _hits(session["findings"], bugs, known)
        for mutation in applied:
            if mutation["kind"] == "bug" and mutation["id"] in reported - fixed:
                _fix(project, mutation)
                fixed.add(mutation["id"])
        _commit(project, "Fix points from the review")
    trial["fixed"] = sorted(fixed)
    return trial


def _decisions(rejects: list[tuple[Item, dict]]) -> str:
    lines = ["Thanks. My decisions on your points:"]
    for item, finding in rejects:
        where = finding["file"] + (
            f" line {finding['line']}" if finding["line"] else ""
        )
        lines.append(
            f"- {where} ({finding['summary']}): reject. Reason: {item.reason} "
            f"Reopen if: {item.reopen_if}."
        )
    lines.append(
        "Everything else stays open, I will fix those points myself. Do not edit "
        "the code."
    )
    return "\n".join(lines)


def _public(session: dict) -> dict:
    keys = ("label", "session_id", "model", "cost", "budget", "subtype", "turns")
    out = {key: session.get(key) for key in keys}
    for key in ("skill", "parsed", "ok", "done", "findings", "error", "result"):
        if key in session:
            out[key] = session[key]
    return out


# ---------------------------------------------------------------- summary


def _ratio(hit: int, total: int, percent: bool = True) -> str:
    if total == 0:
        return "n/a"
    if not percent:
        return f"{hit} of {total}"
    return f"{hit} of {total} ({round(100 * hit / total)} %)"


def metrics(trials: list[dict], arm: str, max_rounds: int) -> dict:
    every = [t for t in trials if t["arm"] == arm]
    if not every:
        return {}
    reviews = [s for t in every for s in t["sessions"] if "parsed" in s]
    mine = [t for t in every if t["valid"]]
    seeded = [t for t in mine if t["kind"] == "seeded"]
    converged = [t for t in seeded if t["converged_round"]]
    rounds = sum(t["converged_round"] for t in converged)
    conv = _ratio(len(converged), len(seeded), percent=False)
    if converged:
        conv += f", mean {rounds / len(converged):.1f} rounds"
    left = sum(t.get("bugs_left_at_done", 0) > 0 for t in converged)
    expect = [t for t in mine if t.get("expected_done") is not None]
    out = {
        "Re-raise rate": _ratio(
            sum(len(t["reraised"]) for t in mine), sum(t["decided"] for t in mine)
        ),
        f"Converged within {max_rounds} rounds": conv,
        "Said done with seeded bugs left": _ratio(left, len(converged), False),
        "Blocker precision": _ratio(
            sum(t.get("true_blockers", 0) for t in mine),
            sum(t.get("blockers", 0) for t in mine),
        ),
        "Blocker recall": _ratio(
            sum(t.get("bugs_hit_blocker", 0) for t in mine),
            sum(t.get("bugs", 0) for t in mine),
        ),
        "Recall, any severity": _ratio(
            sum(t.get("bugs_hit_any", 0) for t in mine),
            sum(t.get("bugs", 0) for t in mine),
        ),
        "Done as expected (eval cases)": _ratio(
            sum(t["done"] == t["expected_done"] for t in expect), len(expect)
        ),
        "Replies without a findings block": _ratio(
            sum(not s["parsed"] for s in reviews), len(reviews), percent=False
        ),
        "Trials left out (failed or no block)": _ratio(
            len(every) - len(mine), len(every), percent=False
        ),
        "Skill fired": "n/a",
        "Cost (USD)": f"{sum(s['cost'] for t in every for s in t['sessions']):.2f}",
    }
    if arm == "judicata":
        fired = sum(bool(s.get("skill")) for s in reviews)
        out["Skill fired"] = f"{fired} of {len(reviews)} sessions"
    return out


def summary_section(run: dict, trials: list[dict]) -> str:
    cases = sorted({t["case"] for t in trials})
    head = [
        f"### {run['date']} (run {run['id']})",
        "",
        (
            f"- judicata {run['plugin_version']} ({run['plugin_commit']}), Claude "
            f"Code {run['claude_version']}, model: "
            f"{', '.join(run['models']) or 'unknown'}"
        ),
        (
            f"- cases: {len(cases)} ({', '.join(cases)}), trials per case and arm: "
            f"{run['trials']}, max rounds: {run['max_rounds']}, seed: {run['seed']}"
        ),
        f"- cost: {run['spent']:.2f} USD of a cap of {run['max_cost_usd']:.2f} USD, "
        f"{run['calls']} sessions"
        + (", stopped at the cost cap (partial results)" if run["stopped"] else ""),
        "",
        "| Metric | judicata | plain Claude |",
        "| --- | --- | --- |",
    ]
    per_arm = {arm: metrics(trials, arm, run["max_rounds"]) for arm in ARMS}
    names = next((list(m) for m in per_arm.values() if m), [])
    rows = [
        f"| {name} | {per_arm['judicata'].get(name, 'n/a')} | "
        f"{per_arm['plain'].get(name, 'n/a')} |"
        for name in names
    ]
    return "\n".join(head + rows) + "\n"


def write_summary(path: Path, section: str) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = f"# Measurement\n\n{MARKER}\n"
    if MARKER not in text:
        raise Usage(f"{path.name} has no line {MARKER}")
    before, after = text.split(MARKER, 1)
    rest = after.lstrip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{before}{MARKER}\n\n{section}" + (f"\n{rest}" if rest else ""),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- main


def _inside_repo(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == REPO or REPO in resolved.parents


def _plugin_info(plugin_dir: Path) -> tuple[str, str]:
    manifest = json.loads(
        (plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    proc = subprocess.run(
        ["git", "describe", "--tags", "--always", "--dirty"],
        cwd=plugin_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return manifest.get("version", "unknown"), proc.stdout.strip() or "no git"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="measure.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--raw-dir", required=True, help="raw results, outside this repo")
    p.add_argument("--summary", default=str(REPO / "docs" / "measurement.md"))
    p.add_argument("--max-cost-usd", type=float, default=5.0, help="cap per run")
    p.add_argument("--max-run-usd", type=float, default=1.5, help="cap per session")
    p.add_argument("--trials", type=int, default=1, help="per case and arm")
    p.add_argument("--max-rounds", type=int, default=3, help="seeded cases")
    p.add_argument("--case", action="append", help="name glob, e.g. 'seeded/*'")
    p.add_argument("--arm", action="append", choices=ARMS, help="default: both")
    p.add_argument("--model", help="passed to claude --model")
    p.add_argument("--seed", default="judicata", help="pick of seeded bugs")
    p.add_argument("--plugin-dir", default=str(REPO), help="judicata checkout")
    p.add_argument("--claude", default="claude", help="the claude executable")
    p.add_argument("--timeout", type=int, default=1800, help="seconds per session")
    p.add_argument("--evals-dir", default=str(REPO / "evals"))
    p.add_argument("--seeded-dir", default=str(REPO / "measure" / "seeded"))
    p.add_argument("--truth", default=str(REPO / "measure" / "evals.json"))
    p.add_argument("--work-dir", help="parent of the throwaway projects")
    p.add_argument("--keep-temp", action="store_true", help="keep the projects")
    p.add_argument(
        "--allow-bash",
        action="store_true",
        help="allow every shell command inside the sandbox (heredocs, variables)",
    )
    p.add_argument("--dry-run", action="store_true", help="show the plan only")
    args = p.parse_args(argv)
    if args.max_cost_usd <= 0 or args.max_run_usd <= 0:
        raise Usage("cost caps must be above 0")
    if args.trials < 1 or args.max_rounds < 1:
        raise Usage("--trials and --max-rounds must be at least 1")
    if _inside_repo(Path(args.raw_dir)):
        raise Usage("--raw-dir must lie outside the judicata repository")
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        cases = load_cases(args)
    except Usage as problem:
        print(f"measure: {problem}", file=sys.stderr)
        return 1
    arms = args.arm or list(ARMS)
    plan = [(c, a, n) for c in cases for a in arms for n in range(1, args.trials + 1)]
    sessions = sum(
        1 if c.kind == "eval" else 2 * args.max_rounds - 1 for c, _, _ in plan
    )
    print(
        f"{len(plan)} trials, at most {sessions} sessions, cap {args.max_cost_usd} USD "
        f"(at most {args.max_run_usd} USD per session)"
    )
    if args.dry_run:
        for case, arm, n in plan:
            print(f"  {case.name} {arm} t{n}")
        return 0
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    raw = Path(args.raw_dir) / run_id
    (raw / "sessions").mkdir(parents=True)
    version, commit = _plugin_info(Path(args.plugin_dir))
    runner = Runner(
        claude=args.claude,
        plugin_dir=Path(args.plugin_dir).resolve(),
        model=args.model,
        max_run=args.max_run_usd,
        max_total=args.max_cost_usd,
        timeout=args.timeout,
        sessions_dir=raw / "sessions",
        allow_bash=args.allow_bash,
    )
    claude_version = runner.version()
    if args.work_dir:
        Path(args.work_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="judicata-measure-", dir=args.work_dir))
    trials: list[dict] = []
    stopped = False
    try:
        for case, arm, n in plan:
            if case.kind == "eval":
                trial = eval_trial(runner, case, arm, n, work)
            else:
                trial = seeded_trial(runner, case, arm, n, work, args)
            trials.append(trial)
            print(f"  {case.name} {arm} t{n}: {runner.spent:.2f} USD so far")
    except CostCap:
        stopped = True
        print("stopped at the cost cap", file=sys.stderr)
    finally:
        if not args.keep_temp:
            shutil.rmtree(work, ignore_errors=True)
    # the last session used up the cap and could not finish: also a cut run
    if runner.spent >= args.max_cost_usd - 1e-9 and any(not t["valid"] for t in trials):
        stopped = True
    run = {
        "id": run_id,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "plugin_version": version,
        "plugin_commit": commit,
        "claude_version": claude_version,
        "models": sorted(runner.models) or ([args.model] if args.model else []),
        "trials": args.trials,
        "max_rounds": args.max_rounds,
        "seed": args.seed,
        "spent": runner.spent,
        "max_cost_usd": args.max_cost_usd,
        "calls": runner.calls,
        "stopped": stopped,
        "arms": arms,
        "cases": [c.name for c in cases],
    }
    (raw / "results.json").write_text(
        json.dumps({"run": run, "trials": trials}, indent=2), encoding="utf-8"
    )
    section = summary_section(run, trials)
    (raw / "summary.md").write_text(section, encoding="utf-8")
    try:
        write_summary(Path(args.summary), section)
    except Usage as problem:
        print(f"measure: {problem}", file=sys.stderr)
        return 1
    print(section)
    return 2 if stopped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
