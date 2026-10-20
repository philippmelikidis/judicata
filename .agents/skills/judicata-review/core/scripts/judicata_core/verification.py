"""Verification of blockers: which ones a verifier challenges, and what its verdict does.

Only confirmed blockers that came from a judgment are verified: findings of a
required judgment criterion the reviewers settled (consensus single or agreed,
not decided by the user) and reviewer findings outside the contract that block
(blocker, consensus single_reviewer or confirmed). Command and pattern criteria
are facts and never verified; optional criteria are should-fix, not blockers.
Skipped as well: a finding the user decided in this round, a suppressed or
represented one (the register), and one the user reopened at the same section
hash (the user's word stands). Unconfirmed blockers (one reviewer of several)
are left to the user here; see select() for where they would join.

Order, most severe first (all candidates are blockers): findings of required
criteria, then reviewer findings by category (security, correctness, factual),
then more sightings first, then label and fingerprint. The first [verifier]
max_runs get a run, the rest are listed as unverified and stay blockers.

Verdicts (verifier_verdict, per round and fingerprint): verified (marked, still
blocks), refuted (only with checked counter-evidence; the finding counts as a
note with the reason until the user reopens it), inconclusive (still blocks).
A refutation without valid counter-evidence is stored as inconclusive.

Events (data fields):
  verifiers_planned        round, contract, model, max_runs, candidates (count),
                           runs [{run, model, **blocker}], unverified [blocker]
                           (blocker = BRIEF fields of the finding)
  verifier_verdict         round, run, model, contract, fingerprint, section_hash,
                           severity (before the verdict), verdict, claimed (what
                           the verifier answered), reason, counter_evidence
                           [{kind quote: file, line, quote} | {kind command:
                           criterion, command, exit_code, output}], dropped
                           [{field, problem}], downgraded (why refuted became
                           inconclusive, or None), independent (false: the
                           session verified itself, no sub-agent)
  verifier_output_rejected round, run, model, attempt, problems
  verifier_run_discarded   round, run, model, reason
  verdict_reopened         round, contract, fingerprint, verdict (event id),
                           section_hash, reason: the user reopened a refuted blocker
"""

from .errors import JudicataError

VERIFIERS_PLANNED = "verifiers_planned"
VERIFIER_VERDICT = "verifier_verdict"
VERIFIER_OUTPUT_REJECTED = "verifier_output_rejected"
VERIFIER_RUN_DISCARDED = "verifier_run_discarded"
VERDICT_REOPENED = "verdict_reopened"

VERDICTS = ("verified", "refuted", "inconclusive")
MAX_ATTEMPTS = 2  # the first output and one retry
SETTLED = ("single", "agreed")  # judgment consensus the reviewers reached alone
CONFIRMED = ("single_reviewer", "confirmed")  # reviewer findings that block
CATEGORY_ORDER = ("security", "correctness", "factual")

BRIEF = (
    "fingerprint",
    "source",
    "criterion",
    "category",
    "label",
    "file",
    "line",
    "section_hash",
    "severity",
    "summary",
)


def refuted(item) -> bool:
    """Whether an entry (or its verification) stands refuted: not reopened."""
    verification = item.get("verification") if "verdict" not in item else item
    return (
        isinstance(verification, dict)
        and verification.get("verdict") == "refuted"
        and not verification.get("reopened")
    )


def public(verification: dict | None) -> dict | None:
    if verification is None:
        return None
    return {
        key: verification.get(key)
        for key in (
            "verdict",
            "claimed",
            "reason",
            "counter_evidence",
            "downgraded",
            "independent",
            "round",
            "run",
            "model",
            "event",
            "ts",
            "reopened",
        )
    }


def reopened(events: list[dict]) -> set[tuple]:
    """(contract, fingerprint, section_hash) of every blocker the user reopened."""
    return {
        (e["data"].get("contract"), e["data"].get("fingerprint"), e["data"].get("section_hash"))
        for e in events
        if e["type"] == VERDICT_REOPENED
    }  # fmt: skip


def select(
    entries: list[dict], results: dict, contract_name: str, round_id: str,
    reopened_ones: set,
) -> list[dict]:  # fmt: skip
    """The blockers of a round to verify, most severe first.

    entries: the round's candidates with their state (rounds.with_state, before
    close); results: the criterion results as close would record them.
    """
    chosen = []
    for entry in entries:
        if entry["severity"] != "blocker" or entry["state"] != "open":
            continue
        if entry.get("presentation") is not None:
            continue
        decision = entry.get("decision")
        if decision is not None and decision.get("round") == round_id:
            continue
        key = (contract_name, entry["fingerprint"], entry.get("section_hash"))
        if key in reopened_ones:
            continue
        if entry["source"] == "criterion":
            result = results.get(entry["criterion"]) or {}
            evidence = (
                result.get("evidence") if result.get("check") == "judgment" else None
            )
            if (
                not isinstance(evidence, dict)
                or evidence.get("consensus") not in SETTLED
            ):
                continue
        elif entry.get("consensus") not in CONFIRMED:
            continue  # unconfirmed blockers would join here (put to the user today)
        chosen.append(entry)
    chosen.sort(key=_order)
    return chosen


def _order(entry: dict) -> tuple:
    if entry["source"] == "criterion":
        kind = 0
    elif entry.get("category") in CATEGORY_ORDER:
        kind = 1 + CATEGORY_ORDER.index(entry["category"])
    else:
        kind = 1 + len(CATEGORY_ORDER)
    return (
        kind,
        -len(entry.get("sightings") or []),
        entry.get("label") or "",
        entry["fingerprint"],
    )


def brief(entry: dict) -> dict:
    return {key: entry.get(key) for key in BRIEF}


def run_states(current_round) -> list[dict]:
    states = []
    plan = current_round.verifier_plan
    for run in plan["runs"] if plan else []:
        attempts = len(current_round.verifier_rejections.get(run["run"], []))
        if run["run"] in current_round.verdicts:
            status = "submitted"
        elif run["run"] in current_round.verifier_discards or attempts >= MAX_ATTEMPTS:
            status = "discarded"
        else:
            status = "waiting"
        states.append(
            {
                "run": run["run"],
                "model": run["model"],
                "fingerprint": run["fingerprint"],
                "status": status,
                "rejected_attempts": attempts,
            }
        )
    return states


def require_complete(current_round) -> None:
    waiting = [s["run"] for s in run_states(current_round) if s["status"] == "waiting"]
    if waiting:
        raise JudicataError(
            "verification_incomplete",
            f"{len(waiting)} verifier run(s) have no verdict yet, submit or discard them",
            {"round": current_round.id, "waiting": waiting},
        )


def summary(current_round, eligible: list[dict]) -> dict:
    """What the verifiers of a round decided, for round close.

    eligible: the blockers that would be verified, only used without a plan
    (then all of them are unverified).
    """
    plan = current_round.verifier_plan
    verdicts = list(current_round.verdicts.values())
    shown = {verdict: [] for verdict in VERDICTS}
    runs = {r["run"]: r for r in plan["runs"]} if plan else {}
    for verdict in verdicts:
        blocker = runs.get(verdict["run"], {})
        shown[verdict["verdict"]].append(
            {
                **{key: blocker.get(key) for key in BRIEF},
                "verification": public(verdict),
            }
        )
    discarded = []
    for state in run_states(current_round):
        if state["status"] != "discarded":
            continue
        manual = current_round.verifier_discards.get(state["run"])
        discarded.append(
            {
                **{key: runs[state["run"]].get(key) for key in BRIEF},
                "run": state["run"],
                "model": state["model"],
                "reason": manual["reason"] if manual else "invalid output twice",
            }
        )
    return {
        "planned": plan is not None,
        "max_runs": plan["max_runs"] if plan else None,
        "candidates": plan["candidates"] if plan else len(eligible),
        **shown,
        "discarded": discarded,
        "unverified": list(plan["unverified"])
        if plan
        else [brief(e) for e in eligible],
        "independent": all(v.get("independent", True) for v in verdicts),
    }
