"""The stop rule: what a closed round means for its pass. A report, not a gate.

Rounds count per contract and scope (kind, paths, base of round_started.scope)
within a pass, in the order they closed. A pass ends with one of ENDS; the next
round of that contract and scope starts a new pass at round 1. A new version of
the contract (a contract_version event before the round started, or another
contract hash) starts a new pass too. Focused rounds (round_closed.focused)
neither count nor end a pass.

After an unfocused round, in this order:
  done           every required criterion passed and no blocking finding is
                 open (failed optional criteria and findings outside the
                 contract may be rejected or accepted: "done with accepted
                 risks"). Failed required criteria whose findings were all
                 rejected or accepted (#33) are never quietly done: the round
                 is done_with_unmet_required only when the user confirmed
                 exactly this set (round finish, or carried over), which ends
                 the pass; otherwise they count as open blockers and the round
                 is required_unmet when nothing else is left (the pass stays
                 open like continue)
  not_converged  in stall_rounds rounds in a row the open blockers did not go
                 below the round before (the first round of a pass is only the
                 starting value: with stall_rounds 2, round 3 at the earliest)
  round_limit    the round is round max_rounds of its pass
  continue       otherwise (required_unmet: only unmet required criteria left;
                 waiting_for_decision: an unattended round closed with open
                 decisions, which count as open blockers, see waiting.py)
A finish confirmed after the round closed (finish_confirmed) turns its
required_unmet into done_with_unmet_required (Round.outcome), which ends the pass.
The limits come from the contract when the round started (round_started.stop;
rounds before #14 use the defaults). open_blockers per round is the count that
round_closed recorded: since #32 one per blocking place (a criterion failing
at four places counts four, round_closed.blockers_counted = "places"); rounds
closed before counted a failed criterion once. Those older counts are not
comparable, so stalled rounds are counted only over the rounds at the end of a
pass that count the same way (comparable()); the list per round shows all.
"""

from . import assignment, contract, narrow, states

REQUIRED_UNMET = "required_unmet"
DONE_UNMET = "done_with_unmet_required"
WAITING = "waiting_for_decision"
ENDS = ("done", DONE_UNMET, "not_converged", "round_limit")
LABELS = {
    "done": "done",
    DONE_UNMET: "finished despite unmet required criteria",
    REQUIRED_UNMET: "required criteria not met",
    WAITING: "waiting for your decision",
    "continue": "continue",
    "not_converged": "not converged",
    "round_limit": "round limit reached",
    "focus_clear": "focus clear",
    "focus_open": "focus open",
}
RISK_SEVERITIES = ("blocker", "should-fix")
CONTRACT_VERSION = "contract_version"


def key(found) -> tuple:
    """The contract and scope a round counts for. The paths are a set: order
    and paths below another given path do not make a new scope."""
    scope = found.scope or narrow.scope_of_contract()
    given = sorted(set(scope["paths"]))
    paths = tuple(
        p for p in given if not assignment.within([q for q in given if q != p])(p)
    )
    return (found.contract, scope["kind"], paths, scope["base"])


LIMITS = ("max_rounds", "stall_rounds")


def limits(found) -> dict:
    """max_rounds and stall_rounds of a round (the gate is no limit)."""
    given = {**contract.DEFAULT_STOP, **(found.stop or {})}
    return {key: given[key] for key in LIMITS}


def focused(found) -> bool:
    closed = found.closed or {}
    return bool(closed.get("focused", found.focus is not None))


def counted(found) -> bool:
    return found.closed is not None and not focused(found)


def versions(events: list[dict], name: str) -> list[int]:
    """Ledger positions of the contract_version events of a contract."""
    return [
        seq
        for seq, event in enumerate(events)
        if event["type"] == CONTRACT_VERSION and event["data"].get("contract") == name
    ]


def epoch(found, version_seqs: list[int]) -> tuple:
    """The contract version a round ran under: versions before it, and its hash."""
    before = sum(1 for seq in version_seqs if seq < found.started_seq)
    return (before, found.contract_hash)


def passes(all_rounds: dict, wanted: tuple, version_seqs: list[int]) -> list[list]:
    """The closed, counted rounds of one contract and scope, split into passes."""
    done = sorted(
        (r for r in all_rounds.values() if counted(r) and key(r) == wanted),
        key=lambda r: r.closed_seq,
    )
    found: list[list] = []
    current: list = []
    for item in done:
        if current and epoch(current[-1], version_seqs) != epoch(item, version_seqs):
            found.append(current)  # a new contract version, a new pass
            current = []
        current.append(item)
        if item.outcome in ENDS:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def ended(rounds_of_pass: list) -> bool:
    return bool(rounds_of_pass) and rounds_of_pass[-1].outcome in ENDS


PER_PLACE = "places"  # round_closed.blockers_counted since #32


def counting(found) -> str | None:
    """How a closed round counted its open blockers (None: per criterion)."""
    value = (found.closed or {}).get("blockers_counted")
    return value if isinstance(value, str) else None


def comparable(kinds: list) -> int:
    """Where the rounds at the end of a pass that count their open blockers
    the same way as the last one start."""
    start = len(kinds)
    while start > 0 and kinds[start - 1] == kinds[-1]:
        start -= 1
    return start


def stalled(counts: list[int]) -> int:
    """How many rounds at the end of a pass did not lower the open blockers
    below the round before. The first round of a pass is the starting value."""
    total = 0
    for index in range(len(counts) - 1, 0, -1):
        if counts[index] < counts[index - 1]:
            break
        total += 1
    return total


def judge(
    events: list[dict], all_rounds: dict, current_round, done: bool, open_blockers: int
) -> dict:
    """The outcome of an unfocused round being closed, and where it stands."""
    seqs = versions(events, current_round.contract)
    earlier = passes(all_rounds, key(current_round), seqs)
    if (
        earlier
        and not ended(earlier[-1])
        and epoch(earlier[-1][-1], seqs) == epoch(current_round, seqs)
    ):
        number, prior = len(earlier), earlier[-1]
    else:
        number, prior = len(earlier) + 1, []
    counts = [blockers(r) for r in prior] + [open_blockers]
    bounds = limits(current_round)
    run = stalled(counts[comparable([counting(r) for r in prior] + [PER_PLACE]) :])
    if done:
        outcome = "done"
    elif run >= bounds["stall_rounds"]:
        outcome = "not_converged"
    elif len(counts) >= bounds["max_rounds"]:
        outcome = "round_limit"
    else:
        outcome = "continue"
    return {
        "outcome": outcome,
        "stop": {
            "counted": True,
            "pass": number,
            "round_in_pass": len(counts),
            **bounds,
            "stalled_rounds": 0 if done else run,
            "open_blockers_per_round": counts,
            "ended": outcome in ENDS,
        },
    }


def not_counted(current_round) -> dict:
    return {"counted": False, **limits(current_round)}


def blockers(found) -> int:
    value = (found.closed or {}).get("open_blockers")
    return value if isinstance(value, int) else 0


def label(outcome: str, risks: int) -> str:
    text = LABELS.get(outcome, outcome)
    if risks and outcome in ("done", "focus_clear"):
        text += " with accepted risks"
    elif risks and outcome == DONE_UNMET:
        text += ", with accepted risks"
    return text


def risk(item: dict) -> dict:
    """A finding in the register (rejected or accepted) as reports show it."""
    decision = item.get("decision") or {}
    return {
        key: item.get(key)
        for key in (
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
        )
    } | {
        "reason": decision.get("reason"),
        "reopen_condition": decision.get("reopen_condition"),
        "decided_in": decision.get("round"),
        "warnings": item.get("warnings", []),  # possible prompt injection
    }


def counts_as_risk(item) -> bool:
    return (
        item.state in states.REGISTER and item.info.get("severity") in RISK_SEVERITIES
    )
