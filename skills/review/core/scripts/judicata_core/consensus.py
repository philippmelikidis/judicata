"""Consensus over the reviewer runs of a round, per judgment criterion.

Status of a criterion:
  single        one reviewer was planned, its verdict stands
  agreed        at least two valid runs, all with the same verdict
  disagreed     the valid runs differ                     -> the user decides
  unconfirmed   only one valid run although more were planned -> the user decides
  decided       the user decided in this round
  carried_over  an earlier decision still holds: same contract, same criterion
                hash, every file it covered unchanged, and no file cited now
                that it did not cover. It also overrules reviewers who now agree
                on the other verdict (what was decided is not argued again).
  no_verdict    no valid run at all, the criterion stays pending
A file is unchanged when its SHA-256 equals the one stored with the decision.
This stays per file on purpose: a judgment criterion is about the whole scope,
so a change next to the cited place can matter. Findings outside the contract
are tied to one place and use section hashes (address.py).
"""

import hashlib

from .assignment import within
from .project import Project

MAX_ATTEMPTS = 2  # the first output and one retry


def run_states(current_round) -> list[dict]:
    states = []
    for run in current_round.plan["runs"]:
        attempts = len(current_round.rejections.get(run["run"], []))
        if run["run"] in current_round.outputs:
            status = "submitted"
        elif run["run"] in current_round.discards or attempts >= MAX_ATTEMPTS:
            status = "discarded"
        else:
            status = "waiting"
        states.append({**run, "status": status, "rejected_attempts": attempts})
    return states


def discarded_runs(current_round) -> list[dict]:
    found = []
    for state in run_states(current_round):
        if state["status"] != "discarded":
            continue
        manual = current_round.discards.get(state["run"])
        reason = manual["reason"] if manual else "invalid output twice"
        found.append({"run": state["run"], "model": state["model"], "reason": reason})
    return found


def assess(project: Project, current_round, all_rounds: dict) -> list[dict]:
    planned = len(current_round.plan["runs"])
    outputs = [
        current_round.outputs[run["run"]]
        for run in current_round.plan["runs"]
        if run["run"] in current_round.outputs
    ]
    assessed = []
    for criterion in current_round.criteria:
        if criterion["check"] != "judgment":
            continue
        verdicts, files = [], {}
        for output in outputs:
            verdict = next(
                (v for v in output["verdicts"] if v["criterion"] == criterion["id"]),
                None,
            )
            if verdict is None:  # submit checks this; only a hand-edited ledger
                continue
            verdicts.append(
                {
                    "run": output["run"],
                    "model": output["model"],
                    "passed": verdict["passed"],
                    "reason": verdict["reason"],
                    "evidence": verdict["evidence"],
                    "places": verdict.get("places", []),
                }
            )
            for item in verdict["evidence"]:
                files.setdefault(item["file"], output["files"].get(item["file"]))
        values = {v["passed"] for v in verdicts}
        entry = {
            "criterion": criterion["id"],
            "text": criterion["text"],
            "required": criterion["required"],
            "verdicts": verdicts,
            "decision": None,
            "needs_decision": False,
        }
        if not verdicts:
            entry.update(status="no_verdict", passed=None)
            assessed.append(entry)
            continue
        if planned == 1 or (len(values) == 1 and len(verdicts) >= 2):
            status = "single" if planned == 1 else "agreed"
            entry.update(status=status, passed=verdicts[0]["passed"])
            earlier = _carried(project, current_round, all_rounds, criterion, files)
            if earlier is not None and earlier["passed"] != entry["passed"]:
                # the user decided otherwise on these unchanged places: that holds
                entry.update(
                    status="carried_over",
                    passed=earlier["passed"],
                    decision=_public(earlier),
                    basis="agreed",
                    files=files,
                )
            assessed.append(entry)
            continue
        entry["basis"] = "disagreed" if len(values) > 1 else "unconfirmed"
        entry["files"] = files
        decision = _latest(
            d
            for d in current_round.decisions
            if d["criterion"] == criterion["id"]
            and d["criterion_hash"] == criterion["hash"]
            and not d.get("lapsed")
        )
        status = "decided"
        if decision is None:
            decision = _carried(project, current_round, all_rounds, criterion, files)
            status = "carried_over"
        if decision is None:
            entry.update(status=entry["basis"], passed=None, needs_decision=True)
        else:
            entry.update(
                status=status, passed=decision["passed"], decision=_public(decision)
            )
        assessed.append(entry)
    return assessed


def _carried(project, current_round, all_rounds, criterion, files) -> dict | None:
    earlier = _latest(
        d
        for other in all_rounds.values()
        if other.id != current_round.id and other.contract == current_round.contract
        for d in other.decisions
        if d["criterion"] == criterion["id"]
        and d["criterion_hash"] == criterion["hash"]
        and not d.get("lapsed")  # a contract version changed the criterion since
    )
    if earlier is None or not earlier["files"]:
        return None
    covered = earlier["files"]
    if not set(files) <= set(covered):
        return None
    if any(files[path] != covered[path] for path in files):
        return None
    if any(file_hash(project, path) != digest for path, digest in covered.items()):
        return None
    if current_round.only is not None:  # a narrowed round: not about other files
        inside = within(current_round.only)
        if not all(inside(path) for path in covered):
            return None
    return earlier


def file_hash(project: Project, relative: str) -> str | None:
    path = project.root / relative
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _latest(decisions) -> dict | None:
    return max(decisions, key=lambda d: d["seq"], default=None)


def _public(decision: dict) -> dict:
    return {
        "event": decision["event"],
        "round": decision["round"],
        "ts": decision["ts"],
        "passed": decision["passed"],
        "reason": decision["reason"],
    }
