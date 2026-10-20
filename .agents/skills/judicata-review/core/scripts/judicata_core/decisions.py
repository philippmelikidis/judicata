"""The user's decisions on findings: finding list and finding decide.

finding list --round R      the findings of a round with their state. Before
                            round close: the candidates (criterion findings of
                            failed criteria, reviewer findings once every run is
                            in), as close would record them. After: the recorded ones.
                            earlier: open confirmed blockers of the contract this
                            round did not see; decide them with their last_round.
finding list --contract C   every finding of a contract with its state, and the
                            register of accepted risks (rejected, accepted).
                            Suppressed sightings (rejected or accepted at an
                            unchanged place, states.py) are not in findings but
                            in suppressed; represented ones (the place changed)
                            are, with previous_decision.
finding decide              fix (reason optional), reject or accept (reason and
                            reopen condition required, one line each). Allowed on
                            an open or represented finding, in the latest round
                            that saw it (stale_round otherwise); within an open
                            round a decision of that same round may be replaced.
                            keep: only for a represented finding, stores its
                            previous decision again for the changed place (reason
                            and reopen condition carried over unless given).
                            judicata never fixes.

Event: finding_decided {round, contract, fingerprint, decision (fix|reject|accept),
reason, reopen_condition, kept (event id of the decision kept, or absent), source, category, criterion, criterion_hash, severity,
consensus, address, label, section_hash, file, line, summary}. The stored
section_hash and criterion_hash let later rounds tell whether the place or the
criterion changed since the decision (#13).
"""

from . import contract, ledger, rounds, states, untrusted
from .errors import JudicataError
from .project import Project

FINDING_DECIDED = states.FINDING_DECIDED
KEEP = "keep"
CHOICES = (*states.DECISIONS, KEEP)
STORED = (
    "source",
    "category",
    "criterion",
    "criterion_hash",
    "severity",
    "consensus",
    "address",
    "label",
    "section_hash",
    "file",
    "line",
    "summary",
)


def list_round(project: Project, round_id: str) -> dict:
    events, all_rounds, current_round = _round(project, round_id)
    entries, complete = _entries(project, events, all_rounds, current_round)
    here = {e["fingerprint"] for e in entries}  # suppressed ones were seen too
    found = states.replay(events, all_rounds)
    earlier = [
        untrusted.shown(item.public())
        for item in states.of_contract(found, current_round.contract)
        if item.state == "open" and item.blocking and item.fingerprint not in here
    ]
    shown = [untrusted.shown(e) for e in entries]  # flagged text only cleaned
    return {
        "round": round_id,
        "contract": current_round.contract,
        "closed": current_round.closed is not None,
        "complete": complete,
        "findings": rounds.presented(shown),
        "decisions_needed": [e["fingerprint"] for e in entries if e["needs_decision"]],
        "suppressed": rounds.suppressed(shown),
        "earlier": earlier,
    }


def list_contract(project: Project, name: str | None) -> dict:
    if name is None:
        name = contract.select(project, None).name
    events = ledger.read(project)
    found = states.replay(events, rounds.replay(events))
    items = [untrusted.shown(item.public()) for item in states.of_contract(found, name)]
    counts = {state: 0 for state in states.STATE_ORDER}
    for item in items:
        counts[item["state"]] += 1
    return {
        "contract": name,
        "findings": items,
        "counts": counts,
        "register": [i for i in items if i["state"] in states.REGISTER],
    }


def decide(
    project: Project,
    round_id: str,
    fingerprint: str,
    decision: str,
    reason: str | None,
    reopen: str | None,
) -> dict:
    reason, reopen = _line(reason), _line(reopen)
    if decision not in CHOICES:
        raise JudicataError(
            "usage",
            "--decision must be fix, reject, accept or keep",
            {"decision": decision},
        )
    if decision not in ("fix", KEEP) and not reason:
        raise JudicataError(
            "reason_required",
            f"{decision} needs a one-line reason (--reason)",
            {"decision": decision},
        )
    if decision not in ("fix", KEEP) and not reopen:
        raise JudicataError(
            "reopen_condition_required",
            f"{decision} needs a reopen condition (--reopen-if)",
            {"decision": decision},
        )
    if decision == "fix" and reopen:
        raise JudicataError(
            "usage", "--reopen-if applies to reject and accept only", {}
        )
    events, all_rounds, current_round = _round(project, round_id)
    if current_round.closed is None and current_round.superseded:
        raise rounds.contract_changed(round_id, current_round.contract)
    if current_round.closed is None and current_round.plan is not None:
        rounds.require_complete(current_round)
    entries, _ = _entries(project, events, all_rounds, current_round)
    entry = next((e for e in entries if e["fingerprint"] == fingerprint), None)
    if entry is None:
        raise JudicataError(
            "finding_not_found",
            f"round {round_id} has no finding {fingerprint!r}",
            {"round": round_id, "fingerprint": fingerprint},
        )
    if current_round.closed is not None:
        found = states.replay(events, all_rounds)
        item = found.get((current_round.contract, fingerprint))
        latest = item.rounds[-1] if item and item.rounds else round_id
        if latest != round_id:
            raise JudicataError(
                "stale_round",
                f"a later round saw {_label(entry)} again, decide it there",
                {"round": round_id, "fingerprint": fingerprint, "latest_round": latest},
            )
    standing = entry["decision"]
    decided_here = standing is not None and standing["round"] == round_id
    replaceable = current_round.closed is None and decided_here
    represented = entry["presentation"] == "represented"
    kept = None
    if decision == KEEP:
        if not represented:
            raise JudicataError(
                "not_represented",
                f"keep applies to a decided finding whose place changed; "
                f"{_label(entry)} is not put back to the user in this round",
                {
                    "round": round_id,
                    "fingerprint": fingerprint,
                    "state": entry["state"],
                    "presentation": entry["presentation"],
                },
            )
        kept = entry["previous_decision"]
        decision = kept["decision"]
        reason = reason or kept["reason"]
        reopen = reopen or kept["reopen_condition"]
    if (
        entry["state"] != "open"
        and not replaceable
        and not (represented and not decided_here)
    ):
        raise JudicataError(
            "finding_not_open",
            f"finding {_label(entry)} is {entry['state']}, only open findings "
            "take a decision",
            {
                "round": round_id,
                "fingerprint": fingerprint,
                "state": entry["state"],
                "decision": standing,
            },
        )
    event = ledger.append(
        project,
        FINDING_DECIDED,
        {
            "round": round_id,
            "contract": current_round.contract,
            "fingerprint": fingerprint,
            "decision": decision,
            "reason": reason,
            "reopen_condition": reopen,
            **({} if kept is None else {"kept": kept["event"]}),
            **{key: entry.get(key) for key in STORED},
        },
    )
    return {
        "round": round_id,
        "fingerprint": fingerprint,
        "label": untrusted.shown(entry)["label"],
        "decision": decision,
        "kept": kept is not None,
        "state": states.DECISIONS[decision],
        "event": event["id"],
    }


def _round(project: Project, round_id: str):
    events = ledger.read(project)
    all_rounds = rounds.replay(events)
    current_round = all_rounds.get(round_id)
    if current_round is None:
        raise JudicataError(
            "round_not_found", f"no round with id {round_id!r}", {"round": round_id}
        )
    return events, all_rounds, current_round


def _entries(project, events, all_rounds, current_round) -> tuple[list[dict], bool]:
    name = current_round.contract
    if current_round.closed is None:
        assessed = rounds.assess(project, current_round, all_rounds)
        found = rounds.simulate(
            events, all_rounds, current_round, assessed, closing=False
        )
        return [
            rounds.with_state(e, name, found, current_round.id)
            for e in assessed["candidates"]
        ], assessed["complete"]
    found = states.replay(events, all_rounds)
    recorded = rounds.recorded(events, current_round.id)
    return [rounds.with_state(e, name, found, current_round.id) for e in recorded], True


def _label(entry: dict) -> str:
    """The label for an error message, cleaned: it may come from the repository."""
    return untrusted.clean(entry.get("label"), 300)


def _line(text: str | None) -> str:
    return " ".join((text or "").split())
