"""Unattended runs (#29): open decisions wait for the user instead of failing.

A run is unattended when the caller says so (`--unattended` on round close and
verify plan), when JUDICATA_UNATTENDED is 1, true, yes or on (other values are
ignored), or in block mode: JUDICATA_GATE=block, else the [stop] gate of the
contract as the round recorded it at its start (JUDICATA_GATE=warn makes a
contract that blocks attended again). Nobody can answer a question then.

An interactive round close refuses with decision_needed while a decision is
open (as before). An unattended one closes: every open decision counts as an
open blocker (so the round is never done, and the stop rule counts it), and
round_closed stores it in `waiting`:

  {kind: "criterion", criterion, criterion_hash, basis}  a judgment criterion
      the reviewers did not settle (basis disagreed, or unconfirmed: only one
      valid run although more were planned); decide with review decide on that
      round, which a closed round takes for exactly these criteria
  {kind: "finding", fingerprint, reason, verifier}  reason unconfirmed (a
      blocker only one reviewer saw) with verifier inconclusive, unverified
      (over the limit), discarded or not_planned; or represented (decided
      earlier, its place changed since; verifier None); decide with finding
      decide on that round

pending() lists what still waits, from the ledger only: per (kind, id) the
latest closed round that stored it (review decide on an older one is
stale_round), while the user has not decided it and nothing settled it: for a
criterion no later round over the same files (covers: the whole contract or
the same scope) has a result with the same hash, and the contract still has
it; a finding is not recorded by a later round, and an unconfirmed one is
still open and unsettled, a represented one still decided (not closed by a
contract change) with no later round over its files. carried(): what earlier
rounds left waiting and the round being closed does not settle counts as its
open blockers too, so no later round is done while a decision waits.
"""

import os

from . import contract, states, stop, untrusted, verification

ENV = "JUDICATA_UNATTENDED"
GATE_ENV = "JUDICATA_GATE"
YES = ("1", "true", "yes", "on")
CRITERION = "criterion"
FINDING = "finding"
OUTCOME = stop.WAITING
VERIFIER_TEXT = {
    "inconclusive": " and the verifier could not decide",
    "unverified": " and it was not verified (over the verifier limit)",
    "discarded": " and its verifier run failed",
    "not_planned": " and it was not verified",
}


def gate(stop_settings: dict | None) -> dict:
    """The gate mode of this run: JUDICATA_GATE if set to warn or block, else
    the given [stop] settings (a contract's, or a round's from its start)."""
    value = os.environ.get(GATE_ENV, "").strip().lower()
    if value in contract.GATES:
        return {"mode": value, "source": GATE_ENV}
    given = {**contract.DEFAULT_STOP, **(stop_settings or {})}
    return {"mode": given["gate"], "source": "contract"}


def unattended(stop_settings: dict | None, option: bool) -> dict:
    """{unattended, reason}: reason option, JUDICATA_UNATTENDED or gate."""
    if option:
        return {"unattended": True, "reason": "option"}
    if os.environ.get(ENV, "").strip().lower() in YES:
        return {"unattended": True, "reason": ENV}
    if gate(stop_settings)["mode"] == "block":
        return {"unattended": True, "reason": "gate"}
    return {"unattended": False, "reason": None}


def records(current_round, assessed: dict, entries: list[dict]) -> list[dict]:
    """The open decisions of a round being closed, as round_closed stores them.

    entries: the round's candidates with their state (rounds.with_state)."""
    judged = {e["criterion"]: e for e in assessed["judged"]}
    found = []
    for criterion_id in assessed["needs_verdict"]:
        criterion = next(c for c in current_round.criteria if c["id"] == criterion_id)
        found.append(
            {
                "kind": CRITERION,
                "criterion": criterion_id,
                "criterion_hash": criterion["hash"],
                "basis": judged[criterion_id]["basis"],
            }
        )
    for entry in entries:
        if not entry["needs_decision"]:
            continue
        represented = entry["presentation"] == "represented"
        found.append(
            {
                "kind": FINDING,
                "fingerprint": entry["fingerprint"],
                "reason": "represented" if represented else "unconfirmed",
                "verifier": None if represented else _verifier(current_round, entry),
            }
        )
    return found


def _verifier(current_round, entry: dict) -> str:
    """Why no verifier settled an unconfirmed blocker."""
    verdict = (entry.get("verification") or {}).get("verdict")
    if verdict == "inconclusive":
        return "inconclusive"
    plan = current_round.verifier_plan
    if plan is None:
        return "not_planned"
    fingerprint = entry["fingerprint"]
    if any(u.get("fingerprint") == fingerprint for u in plan["unverified"]):
        return "unverified"
    runs = [r for r in plan["runs"] if r.get("fingerprint") == fingerprint]
    if runs and not any(
        v.get("fingerprint") == fingerprint for v in current_round.verdicts.values()
    ):
        return "discarded"
    return "not_planned"


def shown(current_round, record: dict, found: dict) -> dict:
    """A stored record as reports show it: what to decide, with the command,
    texts from the ledger cleaned (flagged ones with warnings)."""
    base = {
        "kind": record.get("kind"),
        "round": current_round.id,
        "contract": current_round.contract,
    }
    if record.get("kind") == CRITERION:
        return {**base, **_criterion(current_round, record)}
    return {**base, **_finding(current_round, record, found)}


def _criterion(current_round, record: dict) -> dict:
    criterion_id = record.get("criterion")
    criterion = next((c for c in current_round.criteria if c["id"] == criterion_id), {})
    name = untrusted.clean(criterion_id, 60)
    if record.get("basis") == "unconfirmed":
        why = "Only one reviewer answered although more were planned"
    else:
        why = "The reviewers disagree"
    verdicts = [
        {
            "run": output.get("run"),
            "model": untrusted.clean(output.get("model"), 100),
            "passed": verdict.get("passed"),
            "reason": verdict.get("reason"),
            "evidence": verdict.get("evidence") or [],
        }
        for output in current_round.outputs.values()
        for verdict in output.get("verdicts", [])
        if verdict.get("criterion") == criterion_id
    ]
    judged = untrusted.judged(
        current_round.contract,
        {
            "criterion": criterion_id,
            "text": criterion.get("text"),
            "verdicts": verdicts,
        },
    )
    return {
        "criterion": name,
        "criterion_hash": record.get("criterion_hash"),
        "text": judged.get("text"),
        "required": criterion.get("required"),
        "basis": record.get("basis"),
        "verdicts": judged["verdicts"],
        "question": f"Does {name} pass? {why}.",
        "decide_with": "review decide",
        "warnings": judged["warnings"],
    }


def _finding(current_round, record: dict, found: dict) -> dict:
    fingerprint = record.get("fingerprint")
    item = found.get((current_round.contract, fingerprint))
    reason, verifier = record.get("reason"), record.get("verifier")
    public = {}
    if item is not None:
        public = item.public()
        if reason == "represented":  # checked and cleaned like the finding
            previous = states.presentation(item, current_round.id)[1]
            public["previous_decision"] = states.public_decision(previous)
            public["warnings"] = untrusted.finding(public, item.info_event)
        public = untrusted.shown(public)
    if reason == "represented":
        question = (
            "This finding was decided earlier, but its place changed since. Keep "
            "the decision, fix, reject or accept the risk?"
        )
    else:
        question = (
            "Is this blocker real? Only one reviewer saw it"
            + VERIFIER_TEXT.get(verifier, "")
            + ". Fix, reject or accept the risk?"
        )
    return {
        "fingerprint": fingerprint,
        **{
            key: public.get(key)
            for key in (
                "label",
                "source",
                "category",
                "criterion",
                "severity",
                "summary",
                "file",
                "line",
            )
        },
        "reason": reason,
        "verifier": verifier,
        "previous_decision": public.get("previous_decision"),
        "question": question,
        "decide_with": "finding decide",
        "warnings": public.get("warnings", []),
    }


def pending(all_rounds: dict, found: dict, name: str, current=None) -> list[dict]:
    """What still waits for the user in contract name, oldest round first.

    current: the contract as it is now (None: unknown); a criterion it no
    longer has with the same hash waits no more."""
    entries = []
    for found_round, record in latest(all_rounds, name).values():
        if record.get("kind") == CRITERION:
            still = criterion_waits(all_rounds, found_round, record, current)
        else:
            still = _finding_waits(all_rounds, found_round, record, found)
        if still:
            entries.append(shown(found_round, record, found))
    return entries


def latest(all_rounds: dict, name: str) -> dict[tuple, tuple]:
    """(kind, criterion or fingerprint) -> (the latest closed round that
    stored it, its record), in closing order."""
    closed = sorted(
        (r for r in all_rounds.values() if r.contract == name and r.closed),
        key=lambda r: r.closed_seq,
    )
    found: dict[tuple, tuple] = {}
    for found_round in closed:
        for record in found_round.closed.get("waiting") or []:
            if isinstance(record, dict):
                key = _key(record)
                found.pop(key, None)  # the order of the dict: latest round last
                found[key] = (found_round, record)
    return found


def _key(record: dict) -> tuple:
    return (record.get("kind"), record.get("criterion") or record.get("fingerprint"))


def covers(later, earlier) -> bool:
    """Whether a later round looked at everything an earlier one did: the
    whole contract, or the same scope (stop.key)."""
    return later.only is None or stop.key(later) == stop.key(earlier)


def _after(all_rounds: dict, found_round) -> list:
    """The closed rounds of the contract after this one that cover it."""
    return [
        other
        for other in all_rounds.values()
        if other.contract == found_round.contract
        and other.closed is not None
        and other.closed_seq > found_round.closed_seq
        and covers(other, found_round)
    ]


def criterion_waits(all_rounds: dict, found_round, record: dict, current) -> bool:
    """Whether a criterion of a closed round still waits for the user's verdict."""
    criterion_id, digest = record.get("criterion"), record.get("criterion_hash")
    if any(
        d["criterion"] == criterion_id
        and d["criterion_hash"] == digest
        and not d.get("lapsed")
        for d in found_round.decisions
    ):
        return False
    if current is not None:
        now = current.criterion(criterion_id)
        if now is None or now.hash != digest:
            return False
    return not any(  # a later round over the same files settled it
        _settles(other.results, criterion_id, digest)
        for other in _after(all_rounds, found_round)
    )


def _settles(results: dict, criterion_id: str, digest: str) -> bool:
    result = results.get(criterion_id)
    return result is not None and result.get("criterion_hash") == digest


def criterion_state(all_rounds: dict, found_round, criterion_id: str, current):
    """For review decide on a closed round: "waits" (it left this criterion
    waiting and it still waits), the id of the later round that waits for it
    now, or None."""
    key = (CRITERION, criterion_id)
    record = next(
        (
            r
            for r in (found_round.closed or {}).get("waiting") or []
            if isinstance(r, dict) and _key(r) == key
        ),
        None,
    )
    if record is None:
        return None
    holder, _ = latest(all_rounds, found_round.contract)[key]
    if holder.id != found_round.id:
        return holder.id
    return (
        "waits" if criterion_waits(all_rounds, found_round, record, current) else None
    )


def _finding_waits(all_rounds: dict, found_round, record: dict, found: dict) -> bool:
    item = found.get((found_round.contract, record.get("fingerprint")))
    if item is None or not item.rounds or item.rounds[-1] != found_round.id:
        return False  # gone, or a later round recorded it again
    if record.get("reason") == "represented":
        kind, _ = states.presentation(item, found_round.id)
        return (
            kind == "represented"
            and item.state in states.REGISTER  # not closed by a contract change
            and not states.decided_in(item, found_round.id)
            and not _after(all_rounds, found_round)  # looked again, not seen
        )
    return (
        item.state == "open"
        and not item.settled
        and not verification.refuted({"verification": item.verification})
    )


def carried(
    all_rounds: dict, found: dict, current_round, results: dict, own: list[dict]
) -> list[dict]:
    """Decisions earlier rounds left waiting that the round being closed does
    not settle: they count as its open blockers too (never done while one
    waits). found: the states as if the round closed (rounds.simulate)."""
    mine = {_key(r) for r in own}
    criteria = {c["id"] for c in current_round.criteria}
    entries = []
    for earlier, record in latest(all_rounds, current_round.contract).values():
        if _key(record) in mine:
            continue
        if record.get("kind") == CRITERION:
            if not criterion_waits(all_rounds, earlier, record, None):
                continue
            if record.get("criterion") not in criteria:  # outside the focus
                continue
            if covers(current_round, earlier) and _settles(
                results, record.get("criterion"), record.get("criterion_hash")
            ):
                continue
        else:
            if not _finding_waits(all_rounds, earlier, record, found):
                continue
            item = found[(current_round.contract, record.get("fingerprint"))]
            if not states.in_view(current_round, item.info):
                continue
            if record.get("reason") == "represented" and covers(current_round, earlier):
                continue  # this round looked again and did not see it
        entries.append(shown(earlier, record, found))
    return entries
