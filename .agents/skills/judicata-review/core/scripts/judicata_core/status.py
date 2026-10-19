"""judicata status: where every contract stands, only from the ledger.

status [--contract C] answers per contract (all contract files plus contracts
the ledger knows, sorted by name):
  outcome, outcome_label, scope  of the latest round that counts for the stop
                                 rule (None before the first); the label says
                                 "done with accepted risks" when that round
                                 was done only because findings of optional
                                 criteria or outside the contract were
                                 rejected or accepted; "finished despite
                                 unmet required criteria" for
                                 done_with_unmet_required
  required_unmet  of that round: failed required criteria whose findings the
                  user all rejected or accepted, with the decisions (#33)
  finish_confirmed  {event, round, ts} of the user's confirmation when the
                    outcome is done_with_unmet_required, else None
  contract_changed  a new contract version (contract_version event) or another
                    contract file came after that round (None: no round). Then
                    outcome and outcome_label are None and earlier_outcome
                    {round, outcome, outcome_label, scope} holds the old result
  passes        per scope (latest activity first) the current or last pass:
                pass, round_in_pass, ended, next_round (1 after an end),
                superseded (a newer contract version; next_round is 1),
                max_rounds, stall_rounds, outcome, open_blockers_per_round,
                rounds [{round, outcome, outcome_label, open_blockers, closed_at}]
  last_focus    the latest focused round (it does not count), or None
  open_rounds   rounds started and not closed [{round, scope, focus, failed}],
                failed = criteria that failed so far (recorded at close)
  blockers      blocking findings (open or resolved), criterion and reviewer;
                criterion findings only for criteria the contract file still
                has unchanged (id and hash), when it can be read
  should_fix    live should-fix findings; notes = the number of live notes
  accepted_risks, rejected   the register with reason and reopen condition
  counts        findings per state
Writes nothing, also no .judicata/.
"""

from . import contract, ledger, narrow, rounds, states, stop, untrusted
from .errors import JudicataError
from .project import Project

BRIEF = (
    "fingerprint",
    "label",
    "source",
    "category",
    "criterion",
    "severity",
    "summary",
    "file",
    "line",
    "state",
    "last_round",
)


def show(project: Project, name: str | None) -> dict:
    events = ledger.read(project)
    all_rounds = rounds.replay(events)
    found = states.replay(events, all_rounds)
    names = sorted(
        set(contract.names(project)) | {r.contract for r in all_rounds.values()}
    )
    if name is not None:
        if name not in names:
            raise JudicataError(
                "contract_not_found",
                f"no contract named {name!r}",
                {"contract": name, "contracts": names},
            )
        names = [name]
    return {
        "contracts": [
            _contract(
                n, all_rounds, found, _current(project, n), stop.versions(events, n)
            )
            for n in names
        ]
    }


def _current(project: Project, name: str):
    path = project.contracts_dir / f"{name}.toml"
    if not path.is_file():
        return None
    try:
        return contract.load(path)
    except JudicataError:
        return None


def _contract(
    name: str, all_rounds: dict, found: dict, current, seqs: list[int]
) -> dict:
    own = [r for r in all_rounds.values() if r.contract == name]
    counted = sorted((r for r in own if stop.counted(r)), key=lambda r: r.closed_seq)
    latest = counted[-1] if counted else None

    def superseded(found_round) -> bool:
        before, used = stop.epoch(found_round, seqs)
        return before != len(seqs) or (current is not None and current.hash != used)

    changed = None if latest is None else superseded(latest)
    result = latest if latest is not None and not changed else None
    keys = list(dict.fromkeys(stop.key(r) for r in reversed(counted)))
    focused = sorted(
        (r for r in own if r.closed is not None and stop.focused(r)),
        key=lambda r: r.closed_seq,
    )
    items = [i for i in states.of_contract(found, name) if _applies(i, current)]
    live = [i for i in items if i.state in states.LIVE]
    counts = {state: 0 for state in states.STATE_ORDER}
    for item in items:
        counts[item.state] += 1
    return {
        "contract": name,
        "outcome": _word(result.outcome) if result else None,
        "outcome_label": _label(result) if result else None,
        "scope": _scope(latest.scope) if latest else None,
        # from the ledger: flagged text only cleaned, with warnings (untrusted.py)
        "required_unmet": untrusted.unmet(
            name, (result.closed.get("required_unmet") or []) if result else []
        ),
        "finish_confirmed": _confirmed(result) if result else None,
        "contract_changed": changed,
        "earlier_outcome": _earlier(latest) if changed else None,
        "passes": [
            _pass(stop.passes(all_rounds, key, seqs), superseded) for key in keys
        ],
        "last_focus": _focus(focused[-1]) if focused else None,
        "open_rounds": [_open(r) for r in own if r.closed is None],
        "blockers": [_brief(i) for i in live if i.blocking],
        "should_fix": [
            _brief(i) for i in live if i.info.get("severity") == "should-fix"
        ],
        "notes": sum(1 for i in live if i.info.get("severity") == "note"),
        "accepted_risks": [
            stop.risk(untrusted.shown(i.public()))
            for i in items
            if i.state == "accepted"
        ],
        "rejected": [
            stop.risk(untrusted.shown(i.public()))
            for i in items
            if i.state == "rejected"
        ],
        "counts": counts,
    }


def _pass(found: list[list], superseded) -> dict:
    current = found[-1]
    last = current[-1]
    ended = stop.ended(current)
    old = superseded(last)
    return {
        "scope": _scope(last.scope),
        "pass": len(found),
        "round_in_pass": len(current),
        "ended": ended,
        "superseded": old,
        "next_round": 1 if ended or old else len(current) + 1,
        **stop.limits(last),
        "outcome": _word(last.outcome),
        "outcome_label": _label(last),
        "open_blockers_per_round": [stop.blockers(r) for r in current],
        "rounds": [
            {
                "round": r.id,
                "outcome": _word(r.outcome),
                "outcome_label": _label(r),
                "open_blockers": stop.blockers(r),
                "closed_at": _word(r.closed_ts),
            }
            for r in current
        ],
    }


def _applies(item, current) -> bool:
    """A criterion finding counts while its criterion is in the contract."""
    if current is None or item.info.get("source") != "criterion":
        return True
    criterion = current.criterion(item.info.get("criterion"))
    return criterion is not None and criterion.hash == item.info.get("criterion_hash")


def _open(found) -> dict:
    return {
        "round": found.id,
        "scope": _scope(found.scope),
        "focus": found.focus,
        "failed": [
            c["id"]
            for c in found.criteria
            if c["id"] in found.results and not found.results[c["id"]]["passed"]
        ],
    }


def _earlier(found) -> dict:
    return {
        "round": found.id,
        "outcome": _word(found.outcome),
        "outcome_label": _label(found),
        "scope": _scope(found.scope),
    }


def _focus(found) -> dict:
    return {
        "round": found.id,
        "focus": found.focus,
        "scope": _scope(found.scope),
        "outcome": _word(found.outcome),
        "outcome_label": _label(found),
        "open_blockers": stop.blockers(found),
        "closed_at": _word(found.closed_ts),
    }


def _label(found) -> str:
    closed = found.closed
    risks = closed.get("accepted_risks")
    return _word(stop.label(found.outcome, risks if isinstance(risks, int) else 0))


def _confirmed(found) -> dict | None:
    """The user's confirmation behind a done_with_unmet_required round."""
    if found.outcome != stop.DONE_UNMET:
        return None
    if found.confirmed is not None:
        return {
            "event": _word(found.confirmed["event"]),
            "round": found.id,
            "ts": _word(found.confirmed["ts"]),
        }
    stored = found.closed.get("confirmation")
    if not isinstance(stored, dict):
        return None
    return {key: _word(stored.get(key)) for key in ("event", "round", "ts")}


def _brief(item) -> dict:
    shown = untrusted.shown(item.public())  # flagged text only cleaned
    return {key: shown.get(key) for key in (*BRIEF, "warnings")}


def _word(value):
    """An outcome, label, id or timestamp from the ledger, cleaned (a no-op
    for what judicata writes)."""
    return value if value is None else untrusted.clean(value, 80)


def _scope(stored) -> dict:
    """The scope of a round from the ledger: paths and base cleaned."""
    shown = narrow.public(stored)
    shown["paths"] = [untrusted.clean(p, 300) for p in shown.get("paths") or []]
    if shown.get("base") is not None:
        shown["base"] = untrusted.clean(shown["base"], 100)
    return shown
