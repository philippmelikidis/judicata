"""Review rounds: start, deterministic checks, close.

Events written here (data fields):
  round_started    contract, contract_hash, criteria [{id, text, check, required, hash}]
                   (only those in focus), revision {commit, content, files} (see
                   revision.py), scope {kind, paths, base, merge_base, only},
                   focus {criteria, categories} or None (see narrow.py),
                   stop {max_rounds, stall_rounds} of the contract (stop.py)
  criterion_result round, criterion, criterion_hash, check, passed, evidence,
                   revision (of the files when this check call began), places
                   (failed patterns and references, see findings.pattern_places)
  round_closed     round, outcome (done, continue, not_converged, round_limit,
                   required_unmet, done_with_unmet_required,
                   waiting_for_decision; focus_clear,
                   focus_open), open_blockers (blocking places: every live
                   place of a failed required criterion, every blocking
                   reviewer finding, every waiting decision and every decided
                   place of an unconfirmed unmet required criterion, #32),
                   blockers_counted ("places"; missing: counted per criterion
                   before #32, see stop.py), focused (true:
                   a focused round, not one for the stop rule), pass,
                   round_in_pass (None when focused), accepted_risks (how many
                   rejected or accepted findings of optional criteria or outside
                   the contract let the round pass), required_unmet (failed
                   required criteria whose findings the user all rejected or
                   accepted, see below), unmet_hash, confirmation (the
                   finish_confirmed it carries over, or None), waiting (the
                   open decisions an unattended round left to the user, see
                   waiting.py; [] otherwise), unattended (why the run was
                   unattended: option, JUDICATA_UNATTENDED, gate; or None)
  finish_confirmed round, contract, unmet_hash, required_unmet: the user
                   confirmed finishing a required_unmet round (round finish)
  finding_recorded round, contract, fingerprint, category, severity, consensus,
                   blocking, address, label, section_hash, file, line, summary,
                   sightings [{run, model, proposed, severity, line, symbol,
                   summary, evidence, quote, section_hash}], source
                   (reviewer|criterion; criterion findings add criterion,
                   criterion_hash, check, required, lines)
Finding states and decisions: states.py, decisions.py.
Reviewer events (see review.py) and verifier events (see verification.py) are
replayed here too, onto the same Round.
The round id is the id of its round_started event. Checking reloads the
contract and refuses if it changed. A contract_version of the round's contract
with another hash, recorded after the round started, leaves the round
superseded for good: every step of it refuses with contract_changed (start a
new round). A version that changes or removes a criterion lets the verdict
decisions on it lapse (decision["lapsed"]), also when merged in later.
Starting refuses a contract file edited by hand after its latest recorded
version (contract_edited): the change must be recorded first (contract change).

A failed required criterion is never quietly done (#33). If every finding of it
is rejected or accepted, it does not block, but the round is required_unmet
(never done) and lists it with the decisions. Only round finish --confirm H
(H = unmet_hash of that round) records finish_confirmed; the round is then
done_with_unmet_required, "finished despite unmet required criteria". The hash
covers the contract, each criterion (id, hash) and each of its findings
(fingerprint, state, section hash, decision event): a later round with exactly
the same set carries the confirmation over, anything else asks again. Until
confirmed, the pass stays open like continue.
"""

import hashlib
import json
from dataclasses import dataclass, field

from . import (
    approvals,
    assignment,
    checks,
    consensus,
    contract,
    findings,
    ledger,
    names,
    narrow,
    revision,
    states,
    stop,
    untrusted,
    verification,
    waiting,
)
from .errors import JudicataError
from .project import Project

ROUND_STARTED = "round_started"
CRITERION_RESULT = "criterion_result"
ROUND_CLOSED = "round_closed"
FINDING_RECORDED = "finding_recorded"
REVIEWERS_PLANNED = "reviewers_planned"
REVIEWER_OUTPUT = "reviewer_output"
REVIEWER_OUTPUT_REJECTED = "reviewer_output_rejected"
REVIEWER_RUN_DISCARDED = "reviewer_run_discarded"
VERDICT_DECIDED = "verdict_decided"
FINISH_CONFIRMED = "finish_confirmed"

REQUIRED_UNMET = stop.REQUIRED_UNMET
DONE_UNMET = stop.DONE_UNMET
DONE_UNMET_LABEL = stop.LABELS[DONE_UNMET]


@dataclass
class Round:
    id: str
    contract: str
    contract_hash: str
    criteria: list[dict]
    revision: dict | None = None
    results: dict[str, dict] = field(default_factory=dict)
    closed: dict | None = None
    plan: dict | None = None
    outputs: dict[str, dict] = field(default_factory=dict)  # run -> accepted output
    rejections: dict[str, list[dict]] = field(default_factory=dict)
    discards: dict[str, dict] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)  # in ledger order
    scope: dict | None = None  # None: the contract's files (rounds before #15)
    focus: dict | None = None
    stop: dict | None = None  # the contract's stop rule at round start (#14)
    closed_seq: int = -1  # ledger position of round_closed (stop rule order)
    closed_ts: str | None = None
    started_seq: int = -1  # ledger position of round_started
    superseded: bool = False  # a newer contract version came after the start
    confirmed: dict | None = None  # finish_confirmed of this round
    verifier_plan: dict | None = None  # verifiers_planned (verification.py)
    verdicts: dict[str, dict] = field(default_factory=dict)  # run -> verdict
    # fingerprint -> an earlier round's verdict the plan reuses (verification.py)
    reused: dict[str, dict] = field(default_factory=dict)
    verifier_rejections: dict[str, list[dict]] = field(default_factory=dict)
    verifier_discards: dict[str, dict] = field(default_factory=dict)

    @property
    def only(self) -> list[str] | None:
        return None if self.scope is None else self.scope["only"]

    @property
    def outcome(self) -> str | None:
        """The outcome as it stands: round_closed, or done_with_unmet_required
        once the user confirmed finishing a required_unmet round."""
        if self.closed is None:
            return None
        if self.confirmed is not None and self.closed.get("outcome") == REQUIRED_UNMET:
            return DONE_UNMET
        return self.closed.get("outcome")


def replay(events: list[dict]) -> dict[str, Round]:
    rounds: dict[str, Round] = {}
    known: dict[str, dict[str, str]] = {}  # contract -> criteria of its latest version
    for seq, event in enumerate(events):
        try:
            _apply(rounds, event, seq, known)
        except (KeyError, TypeError) as exc:
            raise JudicataError(
                "ledger_corrupt",
                f"ledger event {event['id']} ({event['type']}) is incomplete: {exc}",
                {"event": event["id"], "type": event["type"]},
            ) from None
    return rounds


def _apply(rounds: dict[str, Round], event: dict, seq: int, known: dict) -> None:
    data = event["data"]
    if event["type"] == ROUND_STARTED:
        rounds[event["id"]] = Round(
            id=event["id"],
            contract=data["contract"],
            contract_hash=data["contract_hash"],
            criteria=[
                {key: item[key] for key in ("id", "text", "check", "required", "hash")}
                for item in data["criteria"]
            ],
            revision=data.get("revision"),
            scope=data.get("scope"),
            focus=data.get("focus"),
            stop=data.get("stop"),
            started_seq=seq,
        )
    elif event["type"] == CRITERION_RESULT and data["round"] in rounds:
        _require(data, "criterion", "passed", "evidence")
        rounds[data["round"]].results.setdefault(data["criterion"], data)
    elif event["type"] == ROUND_CLOSED and data["round"] in rounds:
        rounds[data["round"]].closed = data
        rounds[data["round"]].closed_seq = seq
        rounds[data["round"]].closed_ts = event.get("ts")
    elif event["type"] == FINISH_CONFIRMED and data["round"] in rounds:
        _require(data, "contract", "unmet_hash")
        found = rounds[data["round"]]
        if found.confirmed is None:
            found.confirmed = {**data, "event": event["id"], "ts": event.get("ts")}
    elif event["type"] == states.CONTRACT_VERSION:
        _require(data, "contract", "contract_hash")
        criteria = {c["id"]: c["hash"] for c in data.get("criteria", [])}
        known[data["contract"]] = criteria
        for found in rounds.values():
            if found.contract != data["contract"]:
                continue
            if found.closed is None and data["contract_hash"] != found.contract_hash:
                found.superseded = True  # for good, even if a later version reverts
            for decision in found.decisions:
                _lapse(decision, criteria)
    elif event["type"] == REVIEWERS_PLANNED and data["round"] in rounds:
        _require(data, "source", "runs")
        found = rounds[data["round"]]
        if found.plan is None:
            found.plan = {
                "source": data["source"],
                "paths": data.get("paths"),
                "exclude": data.get("exclude", []),
                "runs": [{"run": r["run"], "model": r["model"]} for r in data["runs"]],
            }
    elif event["type"] == REVIEWER_OUTPUT and data["round"] in rounds:
        _require(data, "run", "model", "verdicts", "files")
        rounds[data["round"]].outputs.setdefault(data["run"], data)
    elif event["type"] == REVIEWER_OUTPUT_REJECTED and data["round"] in rounds:
        _require(data, "run")
        rounds[data["round"]].rejections.setdefault(data["run"], []).append(data)
    elif event["type"] == REVIEWER_RUN_DISCARDED and data["round"] in rounds:
        _require(data, "run", "reason")
        rounds[data["round"]].discards.setdefault(data["run"], data)
    elif event["type"] == verification.VERIFIERS_PLANNED and data["round"] in rounds:
        _require(data, "runs", "unverified", "max_runs", "candidates")
        found = rounds[data["round"]]
        if found.verifier_plan is None:
            found.verifier_plan = {
                **data,
                "runs": list(data["runs"]),
                "unverified": list(data["unverified"]),
            }
            found.reused = verification.reused(data)
    elif event["type"] == verification.VERIFIERS_ADDED and data["round"] in rounds:
        _require(data, "runs")
        plan = rounds[data["round"]].verifier_plan
        if plan is not None:
            known_runs = {r["run"] for r in plan["runs"]}
            added = [r for r in data["runs"] if r["run"] not in known_runs]
            plan["runs"] += added
            gone = {r["fingerprint"] for r in added}
            plan["unverified"] = [
                u for u in plan["unverified"] if u["fingerprint"] not in gone
            ]
    elif event["type"] == verification.VERIFIER_VERDICT and data["round"] in rounds:
        _require(data, "run", "fingerprint", "verdict", "reason")
        found = rounds[data["round"]]
        if data["run"] not in found.verdicts:
            found.verdicts[data["run"]] = {
                **data,
                "event": event["id"],
                "ts": event.get("ts"),
            }
    elif event["type"] == verification.VERDICT_REOPENED and data["round"] in rounds:
        _require(data, "verdict", "reason")
        found = rounds[data["round"]]
        for verdict in [*found.verdicts.values(), *found.reused.values()]:
            if verdict["event"] == data["verdict"] and not verdict.get("reopened"):
                verdict["reopened"] = {
                    "event": event["id"],
                    "ts": event.get("ts"),
                    "reason": data["reason"],
                }
    elif (
        event["type"] == verification.VERIFIER_OUTPUT_REJECTED
        and data["round"] in rounds
    ):
        _require(data, "run")
        rounds[data["round"]].verifier_rejections.setdefault(data["run"], []).append(
            data
        )
    elif (
        event["type"] == verification.VERIFIER_RUN_DISCARDED and data["round"] in rounds
    ):
        _require(data, "run", "reason")
        rounds[data["round"]].verifier_discards.setdefault(data["run"], data)
    elif event["type"] == VERDICT_DECIDED and data["round"] in rounds:
        _require(data, "contract", "criterion", "criterion_hash", "passed", "files")
        decision = {**data, "event": event["id"], "ts": event.get("ts"), "seq": seq}
        if data["contract"] in known:  # merged after a newer version
            _lapse(decision, known[data["contract"]])
        rounds[data["round"]].decisions.append(decision)


def _lapse(decision: dict, criteria: dict[str, str]) -> None:
    """A verdict decision lapses for good once a version changed or removed
    its criterion (consensus ignores lapsed decisions)."""
    if criteria.get(decision["criterion"]) != decision["criterion_hash"]:
        decision["lapsed"] = True


def _require(data: dict, *keys: str) -> None:
    missing = set(keys) - set(data)
    if missing:
        raise KeyError(", ".join(sorted(missing)))


def start(
    project: Project,
    contract_name: str | None,
    paths: list[str] = (),
    base: str | None = None,
    focus_criteria: list[str] | None = None,
    focus_categories: list[str] | None = None,
) -> dict:
    """Start a round, narrowed to a scope and a focus if given (narrow.py)."""
    current = contract.select(project, contract_name)
    _require_recorded(project, current)
    focus = narrow.focus(
        [c.id for c in current.criteria], focus_criteria, focus_categories
    )
    stored = narrow.scope(project, list(paths), base)
    files = assignment.scope(project, current, stored["only"]).files
    if stored["kind"] != "contract" and not files:
        raise narrow.empty_scope(current.name, stored)
    criteria = [
        {
            "id": c.id,
            "text": c.text,
            "check": c.check,
            "required": c.required,
            "hash": c.hash,
        }
        for c in current.criteria
        if focus is None or c.id in focus["criteria"]
    ]
    project.prepare_for_writing()  # .gitattributes first, so it is in the revision
    files = assignment.scope(project, current, stored["only"]).files
    artifact = revision.revision(project, files)
    event = ledger.append(
        project,
        ROUND_STARTED,
        {
            "contract": current.name,
            "contract_hash": current.hash,
            "criteria": criteria,
            "revision": artifact,
            "scope": stored,
            "focus": focus,
            "stop": dict(current.stop),
        },
    )
    return {
        "round": event["id"],
        "contract": current.name,
        "criteria": [untrusted.criterion_text(current.name, c) for c in criteria],
        "revision": artifact,
        "scope": narrow.public(stored, files),
        "focus": focus,
        "warnings": untrusted.contract(current),  # possible prompt injection
    }


def check(project: Project, round_id: str, state_dir: str | None = None) -> dict:
    """Run every deterministic criterion of the round that has no result yet.

    Command criteria run only if the user approved the contract's commands
    (approvals.py); otherwise they stay pending and "approval" lists them.
    """
    current_round = open_round(project, round_id)
    current = contract.select(project, current_round.contract)
    if current.hash != current_round.contract_hash:
        raise contract_changed(round_id, current.name)
    approval = approvals.status(project, current, state_dir)
    results, pending = [], []
    scope = assignment.scope(project, current, current_round.only)
    in_round = {c["id"] for c in current_round.criteria}  # the focus
    artifact = None
    for criterion in current.criteria:
        if criterion.id not in in_round:
            continue
        module = checks.KINDS[criterion.check]
        if not hasattr(module, "run"):
            pending.append(_pending(criterion.__dict__, module))
            continue
        data = current_round.results.get(criterion.id)
        if data is None and criterion.check == "command" and not approval["approved"]:
            pending.append(
                {
                    **_pending(criterion.__dict__, approvals),
                    "command": untrusted.visible(criterion.params["command"]),
                }
            )
            continue
        if data is None:
            if artifact is None:  # what the checks of this call run against
                artifact = revision.revision(project, scope.files)
            outcome = module.run(project, criterion, scope)
            data = {
                "round": round_id,
                "criterion": criterion.id,
                "criterion_hash": criterion.hash,
                "check": criterion.check,
                "passed": outcome["passed"],
                "evidence": outcome["evidence"],
                "revision": artifact,
            }
            if criterion.check in findings.PLACED and not outcome["passed"]:
                whole = findings.scope_place(current.name, artifact["content"])
                data["places"] = findings.pattern_places(
                    project, outcome["evidence"], whole
                )
            ledger.append(project, CRITERION_RESULT, data)
        results.append(_result(criterion.__dict__, data))
    return {
        "round": round_id,
        "contract": current.name,
        "results": results,
        "pending": pending,
        "approval": approval,
        "revision": artifact,
    }


def assess(
    project: Project, current_round: Round, all_rounds: dict, events: list[dict]
) -> dict:
    """Everything a round has found so far, without writing anything.

    complete: every planned run is in (or there is no plan). new_results: the
    judgment results that round close records. needs_verdict: judgment criteria
    the user still has to decide. candidates: the criterion findings of every
    failed criterion and the reviewer findings, in that order.
    """
    complete = current_round.plan is None or all(
        s["status"] != "waiting" for s in consensus.run_states(current_round)
    )
    judged, new_results, needs_verdict, no_verdict = [], {}, [], set()
    if current_round.plan is not None and complete:
        judged = consensus.assess(project, current_round, all_rounds)
        for entry in judged:
            if entry["needs_decision"]:
                needs_verdict.append(entry["criterion"])
            elif entry["passed"] is None:
                no_verdict.add(entry["criterion"])
            elif entry["criterion"] not in current_round.results:
                criterion = _criterion(current_round, entry["criterion"])
                new_results[criterion["id"]] = {
                    "round": current_round.id,
                    "criterion": criterion["id"],
                    "criterion_hash": criterion["hash"],
                    "check": "judgment",
                    "passed": entry["passed"],
                    "evidence": {
                        "consensus": entry["status"],
                        "verdicts": entry["verdicts"],
                        "decision": entry["decision"],
                    },
                }
    results = {**current_round.results, **new_results}
    content = (current_round.revision or {}).get("content")
    candidates = []
    for criterion in current_round.criteria:
        data = results.get(criterion["id"])
        if data is None or data["passed"]:
            continue
        own = (data.get("revision") or {}).get("content") or content
        whole = findings.scope_place(current_round.contract, own)
        candidates += findings.criterion_findings(
            current_round.contract, criterion, data, whole
        )
    if current_round.plan is not None and complete:
        # at the place of a failed deterministic criterion: merged into it (#32)
        candidates += findings.merge(
            candidates,
            findings.assess(current_round),
            _register(events, all_rounds, current_round),
        )
    return {
        "complete": complete,
        "judged": judged,
        "results": results,
        "new_results": new_results,
        "needs_verdict": needs_verdict,
        "no_verdict": no_verdict,
        "candidates": candidates,
    }


def _register(events: list[dict], all_rounds: dict, current_round: Round) -> set:
    """Fingerprints of the contract rejected or accepted when the round started."""
    before = states.replay(events[: current_round.started_seq], all_rounds)
    return {
        item.fingerprint
        for item in states.of_contract(before, current_round.contract)
        if item.state in states.REGISTER
    }


def simulate(
    events: list[dict], all_rounds: dict, current_round: Round, assessed: dict,
    *, closing: bool,
) -> dict:  # fmt: skip
    """Finding states as if round close recorded the candidates (and closed)."""
    current_round.results = assessed["results"]
    extra = [
        {
            "type": FINDING_RECORDED,
            "data": {"round": current_round.id, "contract": current_round.contract}
            | entry,
        }
        for entry in assessed["candidates"]
    ]
    if closing:
        extra.append({"type": ROUND_CLOSED, "data": {"round": current_round.id}})
    return states.replay(events + extra, all_rounds)


def with_state(entry: dict, contract_name: str, found: dict, round_id: str) -> dict:
    """A candidate or recorded finding of a round with its state from states.py.

    presentation: None, "suppressed" (decided at this unchanged place, not put
    to the user) or "represented" (decided, the place changed since; with
    previous_decision). needs_decision: an unconfirmed blocker candidate, or a
    represented blocker or should-fix the user has not decided in this round.
    """
    item = found.get((contract_name, entry["fingerprint"]))
    if item is None:
        shown = {
            **entry,
            "state": "open",
            "decision": None,
            "presentation": None,
            "previous_decision": None,
            "needs_decision": False,
            "verification": None,
        }
        return {**shown, "warnings": untrusted.finding(shown, entry.get("event"))}
    kind, previous = states.presentation(item, round_id)
    unconfirmed = (
        entry["consensus"] == "unconfirmed"
        and item.state == "open"
        and not item.settled  # a verified verdict of this round confirms it
        and not verification.refuted(item.verification or {})
    )
    represented = (
        kind == "represented"
        and entry["severity"] != "note"
        and not states.decided_in(item, round_id)
    )
    shown = {
        **entry,
        "state": item.state,
        "blocking": item.blocking,
        "confirmed": item.settled,
        "decision": states.public_decision(item.decision),
        "presentation": kind,
        "previous_decision": states.public_decision(previous),
        "needs_decision": unconfirmed or represented,
        "verification": verification.public(item.verification),
    }
    if verification.refuted(shown):  # a note while the refutation stands
        shown["severity"] = "note"
    # possible prompt injection in its text or its decisions (untrusted.py)
    return {**shown, "warnings": untrusted.finding(shown, entry.get("event"))}


def presented(entries: list[dict]) -> list[dict]:
    """The entries put to the user: all but the suppressed ones."""
    return [e for e in entries if e["presentation"] != "suppressed"]


def suppressed(entries: list[dict]) -> list[dict]:
    """Suppressed sightings, briefly: decided at an unchanged place."""
    return [
        {
            key: e.get(key)
            for key in (
                "fingerprint",
                "source",
                "category",
                "criterion",
                "severity",
                "label",
                "file",
                "line",
                "summary",
                "state",
                "decision",
            )
        }
        for e in entries
        if e["presentation"] == "suppressed"
    ]


def close(project: Project, round_id: str, unattended: bool = False) -> dict:
    """Evaluate the round: done if every required criterion passed and no
    blocking finding of the contract is open or resolved. A failed required
    criterion whose findings were all rejected or accepted does not block, but
    the round is required_unmet instead of done (or done_with_unmet_required if
    the user confirmed exactly this set before, see finish). Failed optional
    criteria with only decided findings are "decided" (done with accepted risks).

    With planned reviewers, every run must be in (or discarded), every
    disagreement decided and every unconfirmed blocker candidate decided. In
    an unattended run (waiting.py) these open decisions do not refuse: they
    count as open blockers and wait for the user (waiting_for_decision). The
    judgment results and all findings (criterion and reviewer) are recorded
    here, together with round_closed.
    """
    events = ledger.read(project)
    all_rounds = replay(events)
    current_round = open_round(project, round_id, all_rounds)
    if current_round.plan is not None:
        require_complete(current_round)
    if current_round.verifier_plan is not None:
        verification.require_complete(current_round)
    found = evaluate(project, events, all_rounds, current_round, unattended)
    assessed = found["assessed"]
    unconfirmed = found["unconfirmed"]
    mode = found["unattended"]
    if (assessed["needs_verdict"] or unconfirmed) and not mode["unattended"]:
        parts = []
        if assessed["needs_verdict"]:
            parts.append(
                "the reviewers did not settle "
                + ", ".join(assessed["needs_verdict"])
                + ", record the user's decision with review decide"
            )
        if unconfirmed:
            parts.append(
                f"{len(unconfirmed)} finding(s) need the user's decision "
                "(unconfirmed blocker candidates, or decided findings whose place "
                "changed), record it with finding decide"
            )
        raise JudicataError(
            "decision_needed",
            "; ".join(parts) + " first",
            {
                "round": round_id,
                "criteria": assessed["needs_verdict"],
                "findings": unconfirmed,
            },
        )
    contract_name = current_round.contract
    after, shown, view = found["after"], found["shown"], found["view"]
    blockers, should_fix, pending = (
        view["blockers"],
        view["should_fix"],
        view["pending"],
    )
    blocking, outcome, rule = found["blocking"], found["outcome"], found["stop"]
    open_blockers, focused = found["open_blockers"], found["focused"]
    unmet, unmet_hash = found["unmet"], found["unmet_hash"]
    confirmation, risks = found["confirmation"], found["risks"]
    eligible = found["eligible"]
    writes = [(CRITERION_RESULT, data) for data in assessed["new_results"].values()] + [
        (FINDING_RECORDED, {"round": round_id, "contract": contract_name, **entry})
        for entry in assessed["candidates"]
    ]
    ledger.append_many(
        project,
        writes
        + [
            (
                ROUND_CLOSED,
                {
                    "round": round_id,
                    "outcome": outcome,
                    "open_blockers": open_blockers,
                    "focused": focused,
                    "pass": rule.get("pass"),
                    "round_in_pass": rule.get("round_in_pass"),
                    "accepted_risks": len(risks),
                    "required_unmet": [_unmet_record(item) for item in unmet],
                    "unmet_hash": unmet_hash,
                    "confirmation": confirmation,
                    "waiting": found["waits"],
                    "unattended": mode["reason"],
                    "blockers_counted": stop.PER_PLACE,
                },
            )
        ],
    )
    return {
        "round": round_id,
        "contract": contract_name,
        "outcome": outcome,
        "outcome_label": stop.label(outcome, len(risks)),
        "stop": rule,
        "accepted_risks": risks,
        "focused": focused,
        "focus": current_round.focus,
        "scope": narrow.public(current_round.scope),
        "blockers": blockers,
        "should_fix": should_fix,
        "pending": pending,
        "passed": view["passed"],
        "decided": view["decided"],
        "refuted": view["refuted"],
        "required_unmet": unmet,
        "unmet_hash": unmet_hash,
        "confirmation": confirmation,
        "findings": [
            untrusted.shown(e) for e in presented(shown) if e["source"] == "reviewer"
        ],
        "suppressed": suppressed([untrusted.shown(e) for e in shown]),
        "blocking_findings": [untrusted.shown(e) for e in blocking],
        "open_blockers": open_blockers,
        "transitions": [
            {
                "fingerprint": item.fingerprint,
                "label": untrusted.shown({"label": item.info.get("label")})["label"],
                **change,
            }
            for item in states.of_contract(after, contract_name)
            for change in item.history
            if change["round"] == round_id and change["why"] not in states.DECISIONS
        ],
        "reviewers": None
        if current_round.plan is None
        else {
            "runs": consensus.run_states(current_round),
            "discarded": consensus.discarded_runs(current_round),
        },
        "verification": _verification(current_round, eligible),
        "unattended": mode,
        "waiting_for_decision": found["waiting_for_decision"],
    }


def evaluate(
    project: Project,
    events: list[dict],
    all_rounds: dict,
    current_round: Round,
    unattended: bool = False,
) -> dict:
    """Everything round close decides, without writing and without refusing:
    round close records it, round report shows it for an open round (the
    outcome as if the round closed now). unconfirmed: the findings that still
    need the user's decision. unattended (the option; also the environment
    and block mode, waiting.py): those open decisions and the criteria the
    reviewers did not settle go into waits and count as open blockers.
    Decisions earlier unattended rounds left open that this round does not
    settle count in every mode. waiting_for_decision: both, as reports show
    them."""
    round_id = current_round.id
    assessed = assess(project, current_round, all_rounds, events)
    before = simulate(events, all_rounds, current_round, assessed, closing=False)
    eligible = (
        []
        if current_round.verifier_plan is not None
        else verification.select(
            [
                with_state(e, current_round.contract, before, round_id)
                for e in assessed["candidates"]
            ],
            assessed["results"],
            current_round.contract,
            round_id,
            verification.reopened(events),
        )
    )
    entries = [
        with_state(e, current_round.contract, before, round_id)
        for e in assessed["candidates"]
    ]
    unconfirmed = [e["fingerprint"] for e in entries if e["needs_decision"]]
    mode = waiting.unattended(current_round.stop, unattended)
    waits = (
        waiting.records(current_round, assessed, entries) if mode["unattended"] else []
    )
    after = simulate(events, all_rounds, current_round, assessed, closing=True)
    contract_name = current_round.contract
    shown = [
        with_state(e, contract_name, after, round_id) for e in assessed["candidates"]
    ]
    view = _criteria(
        current_round, assessed["results"], shown, after, assessed["no_verdict"]
    )
    required_pending = any(item["required"] for item in view["pending"])
    blocking = [
        item.public()
        for item in states.of_contract(after, contract_name)
        if item.blocking
        and item.info.get("source") == "reviewer"
        # a focused round answers for its focus only; a full one for everything
        and (current_round.focus is None or states.in_view(current_round, item.info))
    ]
    # decisions earlier unattended rounds left open count here too (waiting.py)
    earlier = waiting.carried(
        all_rounds, after, current_round, assessed["results"], waits
    )
    done = (
        not view["blockers"]
        and not blocking
        and not required_pending
        and not waits
        and not earlier
    )
    unmet = view["unmet"]
    unmet_hash = _unmet_hash(contract_name, unmet) if unmet else None
    confirmation = None
    focused = current_round.focus is not None
    # every blocking place counts (#32): a criterion failing at four places is
    # four open blockers, fixing one of them is progress
    open_blockers = (
        sum(item["open_places"] for item in view["blockers"])
        + len(blocking)
        + len(waits)
        + len(earlier)
    )
    if focused:  # a part of the contract is never "done" for all of it
        outcome = "focus_clear" if done and not unmet else "focus_open"
        rule = stop.not_counted(current_round)
    else:  # the stop rule (stop.py); unmet required criteria are never quietly done
        if unmet:
            confirmation = _confirmation(events, contract_name, unmet_hash)
        if unmet and confirmation is None:  # they count until the user confirms
            open_blockers += _unmet_places(unmet, waits)
        finished = done and (not unmet or confirmation is not None)
        judged = stop.judge(events, all_rounds, current_round, finished, open_blockers)
        outcome, rule = judged["outcome"], judged["stop"]
        if outcome == "done" and unmet:
            outcome = DONE_UNMET
        elif outcome == "continue" and (waits or earlier):  # the user decides, later
            outcome = stop.WAITING
        elif outcome == "continue" and done:  # only the unmet ones are left
            outcome = REQUIRED_UNMET
        rule["ended"] = outcome in stop.ENDS
    risks = [{"kind": "criterion", **item} for item in view["decided"]] + [
        {"kind": "finding", **stop.risk(untrusted.shown(item.public()))}
        for item in states.of_contract(after, contract_name)
        if item.info.get("source") == "reviewer"
        and stop.counts_as_risk(item)
        and states.in_view(current_round, item.info)
    ]
    return {
        "assessed": assessed,
        "eligible": eligible,
        "unconfirmed": unconfirmed,
        "after": after,
        "shown": shown,
        "view": view,
        "blocking": blocking,
        "outcome": outcome,
        "stop": rule,
        "open_blockers": open_blockers,
        "focused": focused,
        "unmet": unmet,
        "unmet_hash": unmet_hash,
        "confirmation": confirmation,
        "risks": risks,
        "unattended": mode,
        "waits": waits,
        "waiting_for_decision": [
            waiting.shown(current_round, record, after) for record in waits
        ]
        + earlier,
    }


def _unmet_places(unmet: list[dict], waits: list[dict]) -> int:
    """The decided places of unmet required criteria, each once: a place
    among the waiting decisions counts there already."""
    waiting_findings = {r["fingerprint"] for r in waits if r["kind"] == "finding"}
    return sum(
        1
        for item in unmet
        for f in item["findings"]
        if f["state"] in states.REGISTER and f["fingerprint"] not in waiting_findings
    )


def _verification(current_round: Round, eligible: list[dict]) -> dict:
    """The verifiers' verdicts of the round; texts from the ledger cleaned."""
    found = verification.summary(current_round, eligible)
    for key in (*verification.VERDICTS, "discarded", "unverified"):
        found[key] = [untrusted.shown(entry) for entry in found[key]]
    return found


def _criteria(
    current_round: Round, results: dict, shown: list[dict], found: dict,
    no_verdict: set,
) -> dict:  # fmt: skip
    """Each criterion of the round by its result and the state of its findings.

    passed; pending (no result); for a failed one, with its findings (this
    round's places plus live places of earlier rounds with the same criterion
    hash): blockers (required) and should_fix (optional) while one of them is
    open or resolved and not refuted by a verifier, else refuted (every live
    place refuted, none decided: it does not block), unmet (required: never
    quietly done) or decided (optional).
    """
    contract_name = current_round.contract
    by_criterion: dict[str, list[dict]] = {}
    for entry in shown:
        if entry["source"] == "criterion":
            by_criterion.setdefault(entry["criterion"], []).append(entry)
    view = {
        key: []
        for key in (
            "passed",
            "blockers",
            "should_fix",
            "pending",
            "decided",
            "unmet",
            "refuted",
        )
    }
    for criterion in current_round.criteria:
        data = results.get(criterion["id"])
        if data is None:
            item = _pending(criterion, checks.KINDS.get(criterion["check"]))
            if criterion["id"] in no_verdict:
                item["reason"] = "no valid reviewer output"
            view["pending"].append(item)
            continue
        if data["passed"]:
            view["passed"].append(criterion["id"])
            continue
        own = by_criterion.get(criterion["id"], [])
        here = {e["fingerprint"] for e in own}
        own += [  # live places an earlier round found and this one could not clear
            {**item.public(), "lines": []}
            for item in states.of_contract(found, contract_name)
            if item.fingerprint not in here
            and item.info.get("source") == "criterion"
            and item.info.get("criterion") == criterion["id"]
            and item.info.get("criterion_hash") == criterion["hash"]
            and item.state in states.LIVE
            and states.in_view(current_round, item.info)
        ]
        live = [
            e for e in own if e["state"] in states.LIVE and not verification.refuted(e)
        ]
        disproved = len(live) < len([e for e in own if e["state"] in states.LIVE])
        decided = any(e["state"] in states.REGISTER for e in own)
        severity = "blocker" if criterion["required"] else "should-fix"
        item = {
            **_finding(contract_name, criterion, data, severity),
            "findings": _brief([untrusted.shown(e) for e in own]),
        }
        for brief in item["findings"]:  # its places and decisions, too
            item["warnings"] += [
                w for w in brief["warnings"] if w not in item["warnings"]
            ]
        item["open_places"] = len(live)  # each counts as an open blocker (#32)
        if live:
            view["blockers" if criterion["required"] else "should_fix"].append(item)
        elif disproved and not decided:
            view["refuted"].append(item)
        elif criterion["required"]:
            item["criterion_hash"] = criterion["hash"]
            item["findings"] = [
                {**brief, "section_hash": entry.get("section_hash")}
                for brief, entry in zip(item["findings"], own, strict=True)
            ]
            view["unmet"].append(item)
        else:
            view["decided"].append(item)
    return view


def _unmet_record(item: dict) -> dict:
    """An unmet required criterion as round_closed and finish_confirmed store it."""
    return {
        "criterion": item["criterion"],
        "criterion_hash": item["criterion_hash"],
        "text": item["text"],
        "check": item["check"],
        "findings": [
            {
                "fingerprint": f["fingerprint"],
                "label": f["label"],
                "state": f["state"],
                "section_hash": f["section_hash"],
                "decision": None
                if f["decision"] is None
                else {
                    key: f["decision"].get(key)
                    for key in (
                        "event",
                        "round",
                        "decision",
                        "reason",
                        "reopen_condition",
                    )
                },
            }
            for f in item["findings"]
        ],
    }


def _unmet_hash(contract_name: str, unmet: list[dict]) -> str:
    """What a confirmation holds for: these criteria, places and decisions."""
    key = [
        contract_name,
        sorted(
            [
                item["criterion"],
                item["criterion_hash"],
                sorted(
                    [
                        f["fingerprint"],
                        f["state"],
                        f.get("section_hash"),
                        (f["decision"] or {}).get("event"),
                    ]
                    for f in item["findings"]
                ),
            ]
            for item in unmet
        ),
    ]
    text = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _confirmation(events: list[dict], contract_name: str, unmet_hash: str):
    """The latest finish_confirmed for exactly this set, or None."""
    found = None
    for event in events:
        data = event["data"]
        if (
            event["type"] == FINISH_CONFIRMED
            and data.get("contract") == contract_name
            and data.get("unmet_hash") == unmet_hash
        ):
            found = {
                "event": event["id"],
                "round": data.get("round"),
                "ts": event["ts"],
            }
    return found


def finish(project: Project, round_id: str, confirm: str) -> dict:
    """Confirm finishing a required_unmet round: records finish_confirmed.

    confirm must be the round's unmet_hash (what the user was shown). Refused
    when a later round of the contract (not focused) started, or when anything
    changed since the round looked (finish_outdated): the unmet criteria, their
    places or decisions, a blocking finding of the contract (also from a later
    focused round), the contract, or a file in the round's scope.
    """
    events = ledger.read(project)
    all_rounds = replay(events)
    current_round = all_rounds.get(round_id)
    if current_round is None:
        raise JudicataError(
            "round_not_found", f"no round with id {round_id!r}", {"round": round_id}
        )
    closed = current_round.closed
    if closed is None:
        raise JudicataError(
            "round_open",
            f"round {round_id} is not closed yet, close it first",
            {"round": round_id},
        )
    unmet = closed.get("required_unmet") or []
    if current_round.outcome == DONE_UNMET:
        standing = current_round.confirmed or closed.get("confirmation") or {}
        return _finished(current_round, unmet, standing.get("event"), written=False)
    if closed.get("outcome") != REQUIRED_UNMET:
        raise JudicataError(
            "nothing_to_confirm",
            f"round {round_id} is {closed.get('outcome')}, only a required_unmet "
            "round takes a confirmation",
            {"round": round_id, "outcome": closed.get("outcome")},
        )
    ids = list(all_rounds)
    later = [
        other
        for other in ids[ids.index(round_id) + 1 :]
        if all_rounds[other].contract == current_round.contract
        and all_rounds[other].focus is None
    ]
    if later:
        raise JudicataError(
            "stale_round",
            "a later round of the contract started, decide finishing there",
            {"round": round_id, "latest_round": later[-1]},
        )
    if confirm != closed.get("unmet_hash"):
        raise JudicataError(
            "confirm_mismatch",
            "--confirm must be the unmet_hash round close reported for this round",
            {
                "round": round_id,
                "unmet_hash": closed.get("unmet_hash"),
                "required_unmet": untrusted.unmet(current_round.contract, unmet),
            },
        )
    if _unmet_now(events, all_rounds, current_round) != confirm or _changed_since(
        project, events, current_round
    ):
        raise JudicataError(
            "finish_outdated",
            "the unmet required criteria, their places or decisions changed since "
            "the round closed, start a new round",
            {"round": round_id},
        )
    event = ledger.append(
        project,
        FINISH_CONFIRMED,
        {
            "round": round_id,
            "contract": current_round.contract,
            "unmet_hash": confirm,
            "required_unmet": unmet,
        },
    )
    return _finished(current_round, unmet, event["id"], written=True)


def _finished(current_round: Round, unmet: list, event: str | None, *, written):
    return {
        "round": current_round.id,
        "contract": current_round.contract,
        "outcome": DONE_UNMET,
        "label": DONE_UNMET_LABEL,
        # from the ledger: flagged text only cleaned, with warnings
        "required_unmet": untrusted.unmet(current_round.contract, unmet),
        "event": event,
        "written": written,
    }


def _unmet_now(events: list[dict], all_rounds: dict, current_round: Round):
    """The unmet_hash of a closed round from the ledger as it is now (None if
    a criterion of it blocks again or nothing is unmet any more)."""
    found = states.replay(events, all_rounds)
    shown = [
        with_state(entry, current_round.contract, found, current_round.id)
        for entry in recorded(events, current_round.id)
    ]
    view = _criteria(current_round, current_round.results, shown, found, set())
    blocking = any(
        item.blocking and item.info.get("source") == "reviewer"
        for item in states.of_contract(found, current_round.contract)
    )
    if view["blockers"] or blocking or not view["unmet"]:
        return None
    return _unmet_hash(current_round.contract, view["unmet"])


def _changed_since(project: Project, events: list[dict], current_round: Round):
    """Whether the contract or a file in the round's scope changed since the
    round looked at them (its last check, else its start)."""
    name = current_round.contract
    current = contract.select(project, name)
    latest = None
    for event in events:
        if (
            event["type"] == states.CONTRACT_VERSION
            and event["data"]["contract"] == name
        ):
            latest = event["data"]["contract_hash"]
    if current.hash != current_round.contract_hash:
        return True
    if latest is not None and latest != current_round.contract_hash:
        return True
    files = assignment.scope(project, current, current_round.only).files
    return revision.revision(project, files)["content"] != seen_content(current_round)


def seen_content(found: Round) -> str | None:
    """The content hash of the files a round last looked at: its last check,
    else its start (verdicts from round close carry no revision)."""
    seen = (found.revision or {}).get("content")
    for data in found.results.values():  # in ledger order: the last check
        seen = (data.get("revision") or {}).get("content") or seen
    return seen


def recorded(events: list[dict], round_id: str) -> list[dict]:
    """The findings a closed round recorded, as round close listed them."""
    entries = [
        {k: v for k, v in e["data"].items() if k not in ("round", "contract")}
        | {"event": e["id"]}  # whose text it is (untrusted.py warnings)
        for e in events
        if e["type"] == FINDING_RECORDED and e["data"]["round"] == round_id
    ]
    for entry in entries:
        entry.setdefault("source", "reviewer")
    return entries


def _brief(entries: list[dict]) -> list[dict]:
    return [
        {
            key: e.get(key)
            for key in (
                "fingerprint",
                "label",
                "lines",
                "state",
                "decision",
                "presentation",
                "verification",
                "warnings",
            )
        }
        for e in entries
    ]


def _criterion(current_round: Round, criterion_id: str) -> dict:
    return next(c for c in current_round.criteria if c["id"] == criterion_id)


def require_complete(current_round: Round) -> None:
    waiting = [
        s["run"]
        for s in consensus.run_states(current_round)
        if s["status"] == "waiting"
    ]
    if waiting:
        raise JudicataError(
            "reviews_incomplete",
            f"{len(waiting)} reviewer run(s) have no output yet, submit or discard them",
            {"round": current_round.id, "waiting": waiting},
        )


def open_round(
    project: Project, round_id: str, all_rounds: dict[str, Round] | None = None
) -> Round:
    if all_rounds is None:
        all_rounds = replay(ledger.read(project))
    found = all_rounds.get(round_id)
    if found is None:
        raise JudicataError(
            "round_not_found", f"no round with id {round_id!r}", {"round": round_id}
        )
    if found.closed is not None:
        raise JudicataError(
            "round_closed",
            f"round {round_id} is already closed, start a new round",
            {"round": round_id, "outcome": found.closed["outcome"]},
        )
    if found.superseded:
        raise contract_changed(round_id, found.contract)
    return found


def _require_recorded(project: Project, current: contract.Contract) -> None:
    """A contract with recorded versions is frozen: a file edited by hand after
    the latest one must be recorded (contract change) before it reviews."""
    latest = None
    for event in ledger.read(project):
        data = event["data"]
        if event["type"] == states.CONTRACT_VERSION and data.get("contract") == (
            current.name
        ):
            latest = data
    if latest is not None and latest.get("contract_hash") != current.hash:
        raise JudicataError(
            "contract_edited",
            f"contract {current.name!r} was edited by hand and differs from its "
            f"recorded version {latest.get('version')}; record the change through "
            f"{names.skill('contract')} or restore the file",
            {
                "contract": current.name,
                "file": current.file,
                "latest_version": latest.get("version"),
            },
        )


def contract_changed(round_id: str, name: str) -> JudicataError:
    return JudicataError(
        "contract_changed",
        f"contract {name!r} changed since the round started, start a new round",
        {"round": round_id, "contract": name},
    )


def _result(criterion: dict, data: dict) -> dict:
    # command output and pattern hits come from the artifact: flagged ones cleaned
    evidence, warnings = untrusted.evidence(
        f"criterion {criterion['id']} result", data["evidence"]
    )
    return {
        "criterion": criterion["id"],
        "check": criterion["check"],
        "required": criterion["required"],
        "passed": data["passed"],
        "evidence": evidence,
        "warnings": warnings,
    }


def _finding(contract_name: str, criterion: dict, data: dict, severity: str) -> dict:
    """A failed criterion for the report; flagged texts only cleaned, warnings
    for its text, its parameters and, for a judgment, the verdicts."""
    warnings = untrusted.criterion(contract_name, criterion)
    shown = untrusted.criterion_text(contract_name, criterion)
    evidence = data["evidence"]
    if criterion["check"] == "judgment" and isinstance(evidence, dict):
        judged = untrusted.judged(
            contract_name,
            {
                "criterion": criterion["id"],
                "text": criterion["text"],
                "verdicts": evidence.get("verdicts"),
                "decision": evidence.get("decision"),
            },
        )
        evidence = {
            **evidence,
            "verdicts": judged["verdicts"],
            "decision": judged["decision"],
        }
        warnings += [w for w in judged["warnings"] if w not in warnings]
    else:  # command output, pattern hits: from the artifact
        evidence, found = untrusted.evidence(
            f"criterion {criterion['id']} result", evidence
        )
        warnings += found
    return {
        "criterion": criterion["id"],
        "text": shown["text"],
        "check": criterion["check"],
        "severity": severity,
        "evidence": evidence,
        "warnings": warnings,
    }


def _pending(criterion: dict, module) -> dict:
    return {
        "criterion": criterion["id"],
        "check": criterion["check"],
        "required": criterion["required"],
        "reason": getattr(module, "PENDING_REASON", "not evaluated"),
    }
