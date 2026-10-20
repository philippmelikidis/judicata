"""round report: the whole result of a round, before any decision question (#30).

round report --round R answers, in this order:
  warnings  every possible prompt injection in what follows (untrusted.py)
  result    the one line result: pass, round_in_pass of max_rounds, outcome and
            outcome_label (final false: an open round, as if it closed now),
            severity (counts of the numbered findings by severity),
            open_blockers and open_blockers_per_round of the pass, stalled_rounds,
            suppressed (decided findings seen again at an unchanged place, not
            listed), focused, scope, focus
  criteria  every criterion of the round: status passed, failed, pending or
            undecided (the reviewers disagree, the user decides), effect of a
            failed one (blocker, should-fix, refuted, unmet: required but every
            finding rejected or accepted, accepted: optional, the same), reason
            in one sentence (None when passed), findings (their numbers)
  findings  every finding on its own, numbered from 1, blockers first: source,
            criterion, category, severity, place {file, line, lines, label},
            summary (what is wrong), evidence (scenario, quote, hit or output),
            reviewers {saw, of} (None for deterministic checks), consensus,
            verification (verified, refuted, inconclusive, unverified: over the
            limit, discarded, not_run, None: not a candidate) with
            verifier_reason, history {status new|still_open|came_back|earlier,
            why fix_not_confirmed|place_changed|closed_before|criterion_changed,
            rounds_seen}, state, decision, previous_decision, needs_decision
            (the round cannot close without it), to_decide (the user is asked),
            decide_round (the round to pass to finding decide), fingerprint
  next      decide (numbers to put to the user), criteria_to_decide (criterion
            ids the reviewers did not settle), fix (numbers the user chose to
            fix, the next round confirms them), finish_question (the round is
            required_unmet: the user is asked whether to finish anyway),
            next_round {counted, round_in_pass, pass, max_rounds, rounds_left,
            last_in_pass, not_converged_unless_below, new_pass}
An open round needs every reviewer and verifier run in (reviews_incomplete,
verification_incomplete). Every text comes cleaned (one line, cut); the
report writes nothing.
"""

from . import (
    address,
    findings,
    ledger,
    narrow,
    rounds,
    states,
    stop,
    untrusted,
    verification,
)
from .errors import JudicataError
from .project import Project

SUMMARY = 300
EVIDENCE = 400
LABEL = 200
ASKED = ("blocker", "should-fix")
COUNTING = ("done", stop.DONE_UNMET)


def report(project: Project, round_id: str) -> dict:
    events = ledger.read(project)
    all_rounds = rounds.replay(events)
    current = all_rounds.get(round_id)
    if current is None:
        raise JudicataError(
            "round_not_found", f"no round with id {round_id!r}", {"round": round_id}
        )
    if current.closed is None:
        seen = _open(project, events, all_rounds, current)
    else:
        seen = _closed(events, all_rounds, current)
    prior = states.replay(events[: current.started_seq], all_rounds)
    here = {e["fingerprint"] for e in seen["entries"]}
    earlier = [
        {**item.public(), "earlier": True}
        for item in states.of_contract(seen["found"], current.contract)
        if item.state == "open" and item.blocking and item.fingerprint not in here
    ]
    listed = [e for e in seen["entries"] if e["presentation"] != "suppressed"]
    numbered = _numbered(listed + earlier)
    shown = [_finding(current, seen, prior, entry, n) for n, entry in numbered]
    criteria = [_criterion(current, seen, c, shown) for c in current.criteria]
    rule = seen["stop"]
    counts = {name: 0 for name in findings.SEVERITIES}
    for entry in shown:
        counts[entry["severity"]] = counts.get(entry["severity"], 0) + 1
    warnings = []
    for item in [*criteria, *shown]:
        warnings += [w for w in item["warnings"] if w not in warnings]
    return {
        "round": round_id,
        "contract": untrusted.clean(current.contract, 100),
        "closed": current.closed is not None,
        "warnings": warnings,
        "result": {
            "final": current.closed is not None,
            "outcome": seen["outcome"],
            "outcome_label": seen["outcome_label"],
            "focused": current.focus is not None,
            "counted": rule.get("counted", False),
            "pass": rule.get("pass"),
            "round_in_pass": rule.get("round_in_pass"),
            "max_rounds": rule.get("max_rounds"),
            "stall_rounds": rule.get("stall_rounds"),
            "stalled_rounds": rule.get("stalled_rounds"),
            "ended": bool(rule.get("ended")),
            "severity": counts,
            "suppressed": sum(
                1 for e in seen["entries"] if e["presentation"] == "suppressed"
            ),
            "open_blockers": seen["open_blockers"],
            "open_blockers_per_round": rule.get("open_blockers_per_round"),
            "scope": narrow.public(current.scope),
            "focus": current.focus,
        },
        "criteria": criteria,
        "findings": shown,
        "next": _next(seen, shown, rule),
    }


def _open(project, events, all_rounds, current) -> dict:
    if current.superseded:
        raise rounds.contract_changed(current.id, current.contract)
    if current.plan is not None:
        rounds.require_complete(current)
    if current.verifier_plan is not None:
        verification.require_complete(current)
    found = rounds.evaluate(project, events, all_rounds, current)
    assessed = found["assessed"]
    return {
        "entries": found["shown"],
        "found": found["after"],
        "view": found["view"],
        "results": assessed["results"],
        "judged": {e["criterion"]: e for e in assessed["judged"]},
        "needs_verdict": assessed["needs_verdict"],
        "eligible": {e["fingerprint"] for e in found["eligible"]},
        "outcome": found["outcome"],
        "outcome_label": stop.label(found["outcome"], len(found["risks"])),
        "stop": found["stop"],
        "open_blockers": found["open_blockers"],
    }


def _closed(events, all_rounds, current) -> dict:
    found = states.replay(events, all_rounds)
    entries = [
        rounds.with_state(e, current.contract, found, current.id)
        for e in rounds.recorded(events, current.id)
    ]
    view = rounds._criteria(current, current.results, entries, found, set())
    closed = current.closed
    outcome = current.outcome
    return {
        "entries": entries,
        "found": found,
        "view": view,
        "results": current.results,
        "judged": {},
        "needs_verdict": [],
        "eligible": set(),
        "outcome": outcome,
        "outcome_label": stop.label(outcome, closed.get("accepted_risks") or 0),
        "stop": _rule(events, all_rounds, current),
        "open_blockers": stop.blockers(current),
    }


def _rule(events, all_rounds, current) -> dict:
    """Where a closed round stands in its pass, as round close reported it."""
    if stop.focused(current):
        return stop.not_counted(current)
    seqs = stop.versions(events, current.contract)
    for number, members in enumerate(
        stop.passes(all_rounds, stop.key(current), seqs), 1
    ):
        ids = [r.id for r in members]
        if current.id not in ids:
            continue
        counts = [stop.blockers(r) for r in members[: ids.index(current.id) + 1]]
        return {
            "counted": True,
            "pass": number,
            "round_in_pass": len(counts),
            **stop.limits(current),
            "stalled_rounds": 0
            if current.outcome in COUNTING
            else stop.stalled(counts),
            "open_blockers_per_round": counts,
            "ended": current.outcome in stop.ENDS,
        }
    return stop.not_counted(current)


def _numbered(entries: list[dict]) -> list[tuple[int, dict]]:
    """Blockers first, then should-fix, then notes; criterion findings before
    reviewer findings, then by label (stable within a round)."""
    rank = {name: index for index, name in enumerate(findings.SEVERITIES)}
    ordered = sorted(
        entries,
        key=lambda e: (
            bool(e.get("earlier")),
            rank.get(e.get("severity"), len(rank)),
            e.get("source") != "criterion",
            e.get("criterion") or "",
            e.get("label") or "",
            e.get("fingerprint") or "",
        ),
    )
    return list(enumerate(ordered, 1))


def _finding(current, seen: dict, prior: dict, entry: dict, number: int) -> dict:
    clean = untrusted.clean
    earlier = bool(entry.get("earlier"))
    decision = entry.get("decision")
    summary, evidence, saw = _what(current, seen["results"], entry)
    of = len(current.outputs) if current.plan is not None else 0
    to_decide = bool(entry.get("needs_decision")) or (
        entry.get("state") == "open" and entry.get("severity") in ASKED
    )
    return {
        "number": number,
        "fingerprint": clean(entry.get("fingerprint"), 80),
        "decide_round": clean(entry.get("last_round") if earlier else current.id, 80),
        "source": entry.get("source") or "reviewer",
        "criterion": clean(entry.get("criterion"), 60) or None,
        "category": clean(entry.get("category"), 60),
        "check": entry.get("check"),
        "severity": entry.get("severity"),
        "blocking": bool(entry.get("blocking")),
        "consensus": entry.get("consensus"),
        "place": {
            "file": clean(entry.get("file"), LABEL) or None,
            "line": entry.get("line"),
            "lines": [n for n in entry.get("lines") or [] if isinstance(n, int)],
            "label": clean(entry.get("label"), LABEL),
        },
        "summary": summary,
        "evidence": evidence,
        "reviewers": None if saw is None or earlier else {"saw": saw, "of": of},
        "verification": _verification(current, seen, entry),
        "verifier_reason": clean(
            (entry.get("verification") or {}).get("reason"), SUMMARY
        )
        or None,
        "history": _history(prior, seen["found"], current, entry),
        "state": entry.get("state"),
        "decision": _decision(decision),
        "previous_decision": _decision(entry.get("previous_decision")),
        "needs_decision": bool(entry.get("needs_decision")),
        "to_decide": to_decide,
        "warnings": entry.get("warnings")
        or untrusted.finding(entry, entry.get("event")),
    }


def _decision(decision: dict | None) -> dict | None:
    if not isinstance(decision, dict):
        return None
    return {
        "decision": decision.get("decision"),
        "reason": untrusted.clean(decision.get("reason"), SUMMARY) or None,
        "reopen_condition": untrusted.clean(decision.get("reopen_condition"), SUMMARY)
        or None,
        "round": untrusted.clean(decision.get("round"), 80) or None,
    }


def _what(current, results: dict, entry: dict) -> tuple[str, str, int | None]:
    """(what is wrong, its evidence, how many reviewers saw it) in one line each."""
    clean = untrusted.clean
    if entry.get("source") != "criterion":
        sightings = entry.get("sightings") or []
        first = sightings[0] if sightings else {}
        scenario = first.get("evidence") or ""
        quote = first.get("quote") or ""
        evidence = scenario if not quote else f"{scenario} Quote: {quote}".strip()
        saw = len({s.get("run") for s in sightings}) if sightings else None
        return clean(entry.get("summary"), SUMMARY), clean(evidence, EVIDENCE), saw
    data = results.get(entry.get("criterion")) or {}
    proof = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    check = entry.get("check") or data.get("check")
    text = entry.get("summary") or ""
    file, lines = entry.get("file"), set(entry.get("lines") or [])
    if entry.get("line"):
        lines.add(entry["line"])
    if check == "judgment":
        reasons, quotes, saw = [], [], 0
        for verdict in proof.get("verdicts") or []:
            if verdict.get("passed") is not False:
                continue
            cites = [
                place
                for place in verdict.get("places") or []
                if address.fingerprint(
                    f"criterion:{entry.get('criterion')}", place.get("address")
                )
                == entry.get("fingerprint")
            ]
            if not cites and entry.get("address", {}).get("kind") != "scope":
                continue
            saw += 1
            reasons.append(verdict.get("reason") or "")
            quotes += [
                f"{e.get('file')}:{e.get('line')}: {e.get('quote')}"
                for e in verdict.get("evidence") or []
                if e.get("file") == file and (not lines or e.get("line") in lines)
            ]
        decided = proof.get("decision") or {}
        if not reasons and decided.get("reason"):
            reasons.append(f"decided by the user: {decided.get('reason')}")
        summary = reasons[0] if reasons else text
        evidence = quotes[0] if quotes else ""
        return clean(summary, SUMMARY), clean(evidence, EVIDENCE), saw or None
    if check == "command":
        tail = [line for line in str(proof.get("output_tail") or "").splitlines()]
        tail = [line for line in tail if line.strip()][-3:]
        if proof.get("timed_out"):
            summary = f"{text}: the command timed out"
        else:
            summary = f"{text}: the command exited with code {proof.get('exit_code')}"
        return clean(summary, SUMMARY), clean(" | ".join(tail), EVIDENCE), None
    hits = [
        item
        for key in ("hits", "broken", "missing", "unreadable")
        for item in proof.get(key) or []
    ]
    mine = []
    for item in hits:
        if isinstance(item, str):
            item = {"file": item}
        if not isinstance(item, dict) or item.get("file") != file:
            continue
        if lines and item.get("line") is not None and item.get("line") not in lines:
            continue
        mine.append(item)
    if entry.get("address", {}).get("kind") == "scope":
        summary = f"{text}: fails in more places than listed"
        if proof.get("problem"):
            summary = f"{text}: {proof.get('problem')}"
        return clean(summary, SUMMARY), "", None
    rule = proof.get("rule")
    if rule == "forbid":
        summary = f"{text}: a forbidden pattern is in this place"
    elif rule == "require":
        summary = f"{text}: the required pattern is missing in this file"
    elif rule in ("max_words", "max_lines") and mine:
        unit = "words" if rule == "max_words" else "lines"
        summary = f"{text}: {mine[0].get('count')} {unit}, at most {proof.get('limit')}"
    elif mine and mine[0].get("reason"):
        summary = f"{text}: {mine[0].get('reason')}"
    else:
        summary = text
    parts = []
    for item in mine[:3]:
        where = f"{item.get('file')}:{item.get('line')}" if item.get("line") else ""
        what = item.get("text") or item.get("reference") or item.get("reason") or ""
        parts.append(f"{where}: {what}".strip(": "))
    return clean(summary, SUMMARY), clean(" | ".join(parts), EVIDENCE), None


def _verification(current, seen: dict, entry: dict) -> str | None:
    found = entry.get("verification")
    if isinstance(found, dict) and found.get("verdict"):
        return found["verdict"]
    fingerprint = entry.get("fingerprint")
    plan = current.verifier_plan
    if plan is not None:
        if any(u.get("fingerprint") == fingerprint for u in plan.get("unverified", [])):
            return "unverified"
        if any(r.get("fingerprint") == fingerprint for r in plan.get("runs", [])):
            return "discarded"
        return None
    if fingerprint in seen["eligible"]:
        return "not_run"
    return None


def _history(prior: dict, found: dict, current, entry: dict) -> dict:
    """New, still open, or back (and why), from how the finding stood when
    the round started."""
    key = (current.contract, entry.get("fingerprint"))
    now = found.get(key)
    rounds_seen = len(now.rounds) if now is not None else 1
    if entry.get("earlier"):
        return {"status": "earlier", "why": None, "rounds_seen": rounds_seen}
    before = prior.get(key)
    if before is None or not before.rounds:
        return {"status": "new", "why": None, "rounds_seen": rounds_seen}
    why = None
    if entry.get("presentation") == "represented":
        why = "place_changed"
    elif before.state == "resolved":
        why = "fix_not_confirmed"
    elif before.state == "closed":
        why = "closed_before"
    elif before.state in states.REGISTER:
        why = "criterion_changed"  # the decision lapsed with the criterion
    if why is None:
        return {"status": "still_open", "why": None, "rounds_seen": rounds_seen}
    return {"status": "came_back", "why": why, "rounds_seen": rounds_seen}


def _criterion(current, seen: dict, criterion: dict, shown: list[dict]) -> dict:
    clean = untrusted.clean
    cid = criterion["id"]
    view = seen["view"]
    data = seen["results"].get(cid)
    effect, reason = None, None
    if cid in seen["needs_verdict"]:
        status = "undecided"
        reason = _disagreement(seen["judged"].get(cid) or {})
    elif data is None:
        status = "pending"
        pending = next((p for p in view["pending"] if p["criterion"] == cid), {})
        reason = pending.get("reason") or "not evaluated"
    elif data["passed"]:
        status = "passed"
    else:
        status = "failed"
        for key, name in (
            ("blockers", "blocker"),
            ("should_fix", "should-fix"),
            ("refuted", "refuted"),
            ("unmet", "unmet"),
            ("decided", "accepted"),
        ):
            if any(item["criterion"] == cid for item in view[key]):
                effect = name
                break
        reason = _reason(criterion, data)
    return {
        "criterion": clean(cid, 60),
        "text": clean(criterion["text"], untrusted.CRITERION_LIMIT),
        "check": criterion["check"],
        "required": criterion["required"],
        "status": status,
        "effect": effect,
        "reason": None if reason is None else clean(reason, SUMMARY),
        "findings": [f["number"] for f in shown if f["criterion"] == clean(cid, 60)],
        "warnings": untrusted.criterion(current.contract, criterion),
    }


def _reason(criterion: dict, data: dict) -> str:
    proof = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    check = data.get("check") or criterion["check"]
    if check == "judgment":
        decided = proof.get("decision") or {}
        if decided.get("reason"):
            return f"decided by the user: {decided['reason']}"
        failing = [
            v.get("reason")
            for v in proof.get("verdicts") or []
            if v.get("passed") is False
        ]
        return failing[0] if failing else "the reviewers found it not met"
    if check == "command":
        if proof.get("timed_out"):
            return "the command timed out"
        tail = [
            x for x in str(proof.get("output_tail") or "").splitlines() if x.strip()
        ]
        last = f": {tail[-1]}" if tail else ""
        return f"the command exited with code {proof.get('exit_code')}{last}"
    if proof.get("problem"):
        return str(proof["problem"])
    rule = proof.get("rule")
    if rule == "forbid":
        return f"{proof.get('hit_count', 0)} place(s) match the forbidden pattern"
    if rule == "require":
        return f"the required pattern is missing in {proof.get('missing_count', 0)} file(s)"
    if rule in ("max_words", "max_lines"):
        unit = "words" if rule == "max_words" else "lines"
        return (
            f"{proof.get('hit_count', 0)} file(s) have more than "
            f"{proof.get('limit')} {unit}"
        )
    if "broken_count" in proof:
        return f"{proof['broken_count']} broken reference(s)"
    return "failed"


def _disagreement(judged: dict) -> str:
    verdicts = judged.get("verdicts") or []
    if judged.get("status") == "unconfirmed":
        first = verdicts[0] if verdicts else {}
        side = "passes" if first.get("passed") else "fails"
        return f"only one reviewer answered, it {side}: {first.get('reason') or ''}"
    fail = next((v for v in verdicts if v.get("passed") is False), {})
    passed = next((v for v in verdicts if v.get("passed") is True), {})
    return (
        f"the reviewers disagree: fails ({fail.get('reason') or ''}) / passes "
        f"({passed.get('reason') or ''})"
    )


def _next(seen: dict, shown: list[dict], rule: dict) -> dict:
    outcome = seen["outcome"]
    following = {"counted": bool(rule.get("counted"))}
    if rule.get("counted"):
        ended = bool(rule.get("ended"))
        number = rule.get("round_in_pass") or 0
        bound = rule.get("max_rounds") or 0
        stall = rule.get("stall_rounds") or 0
        risky = not ended and (rule.get("stalled_rounds") or 0) + 1 >= stall
        following |= {
            "new_pass": ended,
            "pass": (rule.get("pass") or 0) + (1 if ended else 0),
            "round_in_pass": 1 if ended else number + 1,
            "max_rounds": bound,
            "rounds_left": bound if ended else max(bound - number, 0),
            "last_in_pass": not ended and number + 1 >= bound,
            "not_converged_unless_below": seen["open_blockers"] if risky else None,
        }
    return {
        "decide": [f["number"] for f in shown if f["to_decide"]],
        "criteria_to_decide": [untrusted.clean(c, 60) for c in seen["needs_verdict"]],
        "fix": [f["number"] for f in shown if f["state"] == "resolved"],
        "finish_question": outcome == stop.REQUIRED_UNMET,
        "next_round": following,
    }
