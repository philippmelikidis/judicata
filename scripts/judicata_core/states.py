"""States of findings, derived only by replaying the ledger.

A finding is one fingerprint within one contract. It is either a criterion
finding (a failed criterion at one place, category = criterion id) or a reviewer
finding (outside the contract, see findings.py). States:

  open      seen and not decided (or reopened)
  resolved  the user chose fix; judicata never fixes, the next round confirms
  closed    a later round of the contract did not see it again and looked for
            it: a criterion finding needs its criterion passed in that round,
            a reviewer finding a reviewer output covering its file
  rejected  the user rejected it (reason and reopen condition required)
  accepted  the user accepted the risk (reason and reopen condition required)

Transitions, in ledger order:
  finding_decided   fix -> resolved, reject -> rejected, accept -> accepted.
                    The decision belongs to its round; it may come before the
                    finding is recorded (decisions are asked before round close).
  finding_recorded  resolved or closed -> open. A decision on a criterion
                    finding lapses when the criterion hash changed (the
                    criterion was edited): -> open. rejected and accepted stay.
  round_closed      open or resolved, not seen in this round, contract of this
                    round, looked for -> closed. A confirmed blocker that was
                    never decided stays open: it waits for the user's word.
  contract_version  a criterion finding whose criterion the new version changed
                    (other hash) or removed -> closed, its decision lapses
                    (criterion_changed, criterion_removed). The changed
                    criterion is judged afresh: failing at the same place again
                    reopens the finding without decision. Decisions on unchanged
                    criteria and on reviewer findings stay. A finding recorded
                    or decided later for a criterion the latest version no
                    longer has in that form (merged from a branch) lapses too.
Only a round that started after the last change of a finding (decision,
closing, reopening) can change it again: a round that began before a fix saw
the artifact before the fix (two open rounds, merged branches).
rejected and accepted form the register of accepted risks.

Verification (verification.py): a verifier_verdict belongs to one round and
fingerprint; the round's finding_recorded attaches it (verification), also an
earlier verdict the round's verifiers_planned reuses (marked reused). A refuted
one makes the finding a note while it stands: not blocking, severity note. A
verified one confirms an unconfirmed finding. A later round that records the
finding again without a verdict of its own starts without one. A
verdict_reopened (the user disagrees) gives the finding its severity back.

Presentation of a sighting of a rejected or accepted finding (per round, set
when the round records it, against the decision that stood before the round's
own decisions):
  suppressed   same section hash as the decision: only a sighting, the round
               does not put it to the user (finding list and round close list
               it under "suppressed")
  represented  the section changed since the decision: put to the user with the
               previous decision. A blocker or should-fix needs a decision in
               this round (keep, fix, reject, accept); "keep" stores the old
               decision again with the new section hash. The state stays
               rejected or accepted until the user decides otherwise.

A finding is blocking while it is open or resolved, its severity is blocker and
it is confirmed: a criterion finding, a confirmed finding, the only reviewer's,
or an unconfirmed one the user confirmed (fix or accept).
"""

from dataclasses import dataclass, field

from . import assignment, scope, untrusted, verification

ROUND_STARTED = "round_started"
FINDING_RECORDED = "finding_recorded"
FINDING_DECIDED = "finding_decided"
ROUND_CLOSED = "round_closed"
CONTRACT_VERSION = "contract_version"

DECISIONS = {"fix": "resolved", "reject": "rejected", "accept": "accepted"}
REGISTER = ("rejected", "accepted")
LIVE = ("open", "resolved")
CONFIRMING = ("criterion", "single_reviewer", "confirmed")
STATE_ORDER = ("open", "resolved", "accepted", "rejected", "closed")

# fields of a finding that the latest recording (or decision) sets
FIELDS = (
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


@dataclass
class Finding:
    contract: str
    fingerprint: str
    info: dict = field(default_factory=dict)
    info_event: str | None = None  # the event whose text info holds (warnings)
    state: str = "open"
    confirmed: bool = False
    decision: dict | None = None  # the standing decision
    decisions: list[dict] = field(default_factory=list)  # every one, in order
    rounds: list[str] = field(default_factory=list)  # rounds that saw it
    since: int = -1  # ledger position of the last change; older rounds cannot move it
    history: list[dict] = field(default_factory=list)  # {round, from, to, why}
    # round id -> {"kind": "suppressed"|"represented", "previous": decision}
    presentations: dict[str, dict] = field(default_factory=dict)
    verification: dict | None = None  # the verifier's verdict of the latest round

    @property
    def blocking(self) -> bool:
        return (
            self.state in LIVE
            and self.info.get("severity") == "blocker"
            and self.confirmed
        )

    def move(self, to: str, round_id: str, why: str, at: int) -> None:
        self.since = at
        if to != self.state:
            self.history.append(
                {"round": round_id, "from": self.state, "to": to, "why": why}
            )
            self.state = to

    def public(self) -> dict:
        shown = {
            "contract": self.contract,
            "fingerprint": self.fingerprint,
            **{key: self.info.get(key) for key in FIELDS},
            "state": self.state,
            "blocking": self.blocking,
            "confirmed": self.confirmed,
            "decision": public_decision(self.decision),
            "rounds_seen": len(self.rounds),
            "last_round": self.rounds[-1] if self.rounds else None,
            "history": self.history,
            "verification": verification.public(self.verification),
        }
        # possible prompt injection in its text or its decision (untrusted.py)
        shown["warnings"] = untrusted.finding(shown, self.info_event)
        return shown

    def warnings(self) -> list[dict]:
        return self.public()["warnings"]


def public_decision(decision: dict | None) -> dict | None:
    if decision is None:
        return None
    return {
        key: decision.get(key)
        for key in (
            "event",
            "ts",
            "round",
            "decision",
            "kept",
            "reason",
            "reopen_condition",
            "section_hash",
            "criterion_hash",
        )
    }


def replay(events: list[dict], all_rounds: dict) -> dict[tuple[str, str], Finding]:
    """(contract, fingerprint) -> Finding. all_rounds comes from rounds.replay."""
    found: dict[tuple[str, str], Finding] = {}
    started: dict[str, int] = {}  # round id -> ledger position of round_started
    known: dict[str, dict[str, str]] = {}  # contract -> criteria of its latest version
    verdicts: dict[tuple, dict] = {}  # (round, contract, fingerprint) -> verdict
    for seq, event in enumerate(events):
        data = event["data"]
        if event["type"] == ROUND_STARTED:
            started[event["id"]] = seq
        elif event["type"] == verification.VERIFIERS_PLANNED:
            for fingerprint, verdict in verification.reused(data).items():
                key = (data.get("round"), data.get("contract"), fingerprint)
                verdicts.setdefault(key, verdict)
        elif event["type"] in (
            verification.VERIFIER_VERDICT,
            verification.VERDICT_REOPENED,
        ):
            _verified(found, verdicts, event)
        elif event["type"] == FINDING_DECIDED:
            _decided(found, event, seq)
            _stale(found, known, data, seq)
        elif event["type"] == FINDING_RECORDED:
            item = _get(found, data["contract"], data["fingerprint"])
            _recorded(item, data, started.get(data["round"], seq), event.get("id"))
            _verify(item, data, verdicts)
            _stale(found, known, data, seq)
        elif event["type"] == ROUND_CLOSED and data["round"] in all_rounds:
            _closed(found, all_rounds[data["round"]], started.get(data["round"], seq))
        elif event["type"] == CONTRACT_VERSION:
            known[data["contract"]] = {
                c["id"]: c["hash"] for c in data.get("criteria", [])
            }
            _versioned(found, data, seq)
    return found


def _stale(found: dict, known: dict, data: dict, seq: int) -> None:
    """A finding recorded or decided for a criterion that a version already
    changed or removed (a merged branch) lapses at once."""
    criteria = known.get(data["contract"])
    if criteria is None:
        return
    item = found[(data["contract"], data["fingerprint"])]
    why = lapse_reason(item, criteria)
    if why is not None:
        item.decision = None
        item.move("closed", data.get("round"), why, seq)


def lapse_reason(item: "Finding", criteria: dict[str, str]) -> str | None:
    """Why a contract version with these criteria (id -> hash) closes the
    finding: criterion_removed, criterion_changed, or None (it stays)."""
    if item.info.get("source") != "criterion" or item.state == "closed":
        return None
    if item.info.get("criterion") not in criteria:
        return "criterion_removed"
    if criteria[item.info["criterion"]] != item.info.get("criterion_hash"):
        return "criterion_changed"
    return None


def _versioned(found: dict, data: dict, seq: int) -> None:
    criteria = {c["id"]: c["hash"] for c in data.get("criteria", [])}
    for item in found.values():
        if item.contract != data["contract"]:
            continue
        why = lapse_reason(item, criteria)
        if why is not None:
            item.decision = None
            item.move("closed", None, why, seq)


def _get(found: dict, contract: str, fingerprint: str) -> Finding:
    key = (contract, fingerprint)
    if key not in found:
        found[key] = Finding(contract, fingerprint)
    return found[key]


def _info(data: dict) -> dict:
    info = {key: data.get(key) for key in FIELDS}
    info["source"] = info["source"] or "reviewer"  # recorded before #12
    return info


def _decided(found: dict, event: dict, seq: int) -> None:
    data = event["data"]
    decision = {**data, "event": event["id"], "ts": event["ts"]}
    item = _get(found, data["contract"], data["fingerprint"])
    # the decision carries the finding as the user saw it, in the latest round
    # that saw it: its severity counts (a note that came back as a blocker)
    item.info = _info(data)
    item.info_event = event["id"]
    decision["seq"] = seq
    # what stood before: a round's first decision tells what the round found
    decision["before"] = (item.state, item.decision)
    item.decisions.append(decision)
    item.decision = decision
    if data["decision"] in ("fix", "accept"):
        item.confirmed = True  # the user says it is real
    item.move(DECISIONS[data["decision"]], data["round"], data["decision"], seq)


def _verified(found: dict, verdicts: dict, event: dict) -> None:
    """A verdict, or the user reopening a refuted one, of a round."""
    data = event["data"]
    key = (data.get("round"), data.get("contract"), data.get("fingerprint"))
    if event["type"] == verification.VERIFIER_VERDICT:
        verdicts[key] = {**data, "event": event["id"], "ts": event.get("ts")}
    elif key in verdicts and verdicts[key]["event"] == data.get("verdict"):
        verdicts[key]["reopened"] = {
            "event": event["id"],
            "ts": event.get("ts"),
            "reason": data.get("reason"),
        }
    else:
        return
    item = found.get(key[1:])
    if item is not None and item.rounds and item.rounds[-1] == key[0]:
        _verify(item, {"round": key[0]}, verdicts, recorded=False)


def _verify(item: Finding, data: dict, verdicts: dict, recorded=True) -> None:
    """Attach the verdict of the round that recorded the finding last."""
    if item.rounds and item.rounds[-1] != data["round"]:
        return  # an older round's record (merged late): its verdict is old too
    if recorded and item.presentations.get(data["round"], {}).get("kind") == (
        "suppressed"
    ):
        return  # only a sighting of a decided finding
    found = verdicts.get((data["round"], item.contract, item.fingerprint))
    if found is None:
        item.verification = None
        return
    item.verification = found
    if found.get("verdict") == "verified":
        item.confirmed = True  # it survived the challenge: as real as a second reviewer
    if verification.refuted(found):
        item.info["severity"] = "note"
    elif found.get("severity") and item.info.get("severity") == "note":
        item.info["severity"] = found["severity"]  # reopened: as it was


def _recorded(item: Finding, data: dict, started: int, event_id=None) -> None:
    round_id = data["round"]
    if round_id not in item.rounds:
        item.rounds.append(round_id)
    presentation = _presentation(item, data, started)
    if presentation is not None:
        item.presentations[round_id] = presentation
        if presentation["kind"] == "suppressed" and item.info:
            # only a sighting: the finding stays as the user decided it (its
            # severity, summary), whatever the reviewer called it this time
            return
    if data.get("consensus") in CONFIRMING:
        item.confirmed = True
    if started < item.since:
        # the round began before the last change: an older look, keep the state
        if not item.info:
            item.info, item.info_event = _info(data), event_id
        return
    item.info, item.info_event = _info(data), event_id
    decision = item.decision
    if (
        decision is not None
        and item.info["source"] == "criterion"
        and decision.get("criterion_hash") != item.info["criterion_hash"]
    ):
        item.decision = None
        item.move("open", round_id, "criterion_changed", started)
    elif item.state in ("resolved", "closed"):
        item.move("open", round_id, "seen_again", started)


def _presentation(item: Finding, data: dict, started: int) -> dict | None:
    """Suppressed or represented, if the finding stood rejected or accepted.

    A decision made after the round started is newer than the round's look at
    the place: it holds, the round does not ask again (parallel rounds).
    """
    state, standing = item.state, item.decision
    own = next((d for d in item.decisions if d["round"] == data["round"]), None)
    if own is not None:  # decided in this round: judge against what stood before
        state, standing = own["before"]
    if state not in REGISTER or standing is None:
        return None
    if standing.get("seq", -1) > started and standing["round"] != data["round"]:
        return {"kind": "suppressed", "previous": standing}
    if data.get("source") == "criterion" and standing.get("criterion_hash") != data.get(
        "criterion_hash"
    ):
        return None  # the decision lapses with the criterion
    same = standing.get("section_hash") == data.get("section_hash")
    return {"kind": "suppressed" if same else "represented", "previous": standing}


def presentation(item: Finding, round_id: str) -> tuple[str | None, dict | None]:
    """(kind, previous decision) of the finding in a round."""
    found = item.presentations.get(round_id)
    if found is None:
        return None, None
    return found["kind"], found["previous"]


def decided_in(item: Finding, round_id: str) -> bool:
    return item.decision is not None and item.decision["round"] == round_id


def _closed(found: dict, current_round, started: int) -> None:
    for item in found.values():
        if item.contract != current_round.contract or item.state not in LIVE:
            continue
        if current_round.id in item.rounds or started < item.since:
            continue
        if (
            item.info.get("source") == "reviewer"
            and item.state == "open"
            and item.blocking
            and item.decision is None
        ):
            continue  # a confirmed blocker closes only after the user's fix
        if looked(current_round, item.info):
            item.move("closed", current_round.id, "not_seen", started)


def looked(current_round, info: dict) -> bool:
    """Whether the round looked for this finding (only from ledger data).

    A criterion finding: its criterion passed, or a pattern criterion failed
    with every place listed (no scope place for a truncated list), so a place
    missing from the list does not fail any more. A reviewer finding: some
    reviewer output came in, the plan covered its file and the focus its
    category. A narrowed round (scope.only) looks only at its files: places
    outside them, and the whole-scope place, stay as they are.
    """
    if not in_view(current_round, info):
        return False
    if info.get("source") == "criterion":
        result = current_round.results.get(info.get("criterion"))
        if not result:
            return False
        if result["passed"]:
            return True
        places = result.get("places")
        return (
            result.get("check") == "pattern"
            and bool(places)
            and all(p["address"].get("kind") != "scope" for p in places)
        )
    plan = current_round.plan
    if plan is None or not current_round.outputs:
        return False
    file = info.get("file")
    if not file or file in plan.get("exclude", []):
        return False
    paths = plan.get("paths")
    return not paths or scope.matcher(paths)(file)


def in_view(current_round, info: dict) -> bool:
    """Whether the finding lies in the round's narrowed files and focus."""
    only = getattr(current_round, "only", None)
    if only is not None:
        file = info.get("file")
        if not file or not assignment.within(only)(file):
            return False
    focus = getattr(current_round, "focus", None)
    if focus is not None and info.get("source") != "criterion":
        return info.get("category") in focus["categories"]
    return True


def of_contract(found: dict, contract: str) -> list[Finding]:
    items = [f for f in found.values() if f.contract == contract]
    items.sort(
        key=lambda f: (
            STATE_ORDER.index(f.state),
            not f.blocking,
            f.info.get("label") or "",
            f.fingerprint,
        )
    )
    return items
