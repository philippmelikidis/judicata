"""Findings outside the contract: severity by the rules, consensus over the runs.

A reviewer reports each finding with category, proposed severity, file, line,
symbol, a one-line summary, a quote and, ideally, evidence (the concrete
scenario). review submit adds address, label, section_hash and fingerprint
(address.py) and stores it in the sighting (reviewer_output.findings).

Severity of one sighting: the proposed one if the category is heavy
(correctness, security, factual) and evidence is given, otherwise note.

Consensus per fingerprint over the runs of a round (a run counts once):
  single_reviewer  one run was planned: its severity stands
  confirmed        two or more runs report it (same category, same address): the
                   highest severity among them stands
  unconfirmed      only one of several runs reports it and it is a blocker; put to
                   the user, round close waits for the decision (fix or accept
                   confirms it, reject rejects it; states.py)
  single_sighting  only one of several runs reports it, no blocker: note
A finding is blocking when it is a blocker that is confirmed or comes from the
only planned reviewer. round close records every finding as finding_recorded.

Criterion findings: a failed criterion becomes one finding per place, so it runs
through the same states (states.py). Category = criterion id, consensus
"criterion", severity blocker (required) or should-fix (optional). Places:
  judgment  the cited places of the failing verdicts (address from submit)
  pattern   forbid: the section of each hit; require: each file missing the
            match; max_words, max_lines, unreadable files: the file. A truncated
            list adds the scope place for the rest.
  command   the scope place {kind: "scope"}, hashed by the revision content
A failed criterion without any place gets the scope place. The fingerprint
uses "criterion:<id>" as category ("criterion:" never starts a taxonomy word or
an id), so a criterion finding never collides with a reviewer finding.

No double finding (#32): a reviewer finding at the same file and line as a
criterion finding of a deterministic check (command, pattern, references) is
merged into it (merge(): its evidence kept in the criterion finding's merged
list), not recorded, counted or put to the user on its own; one more severe
than the criterion finding stays on its own. Command criteria have only the
scope place, so nothing ever lands on them.
"""

from . import address

HEAVY = ("correctness", "security", "factual")
CATEGORIES = HEAVY + ("clarity", "style", "maintainability", "other")  # as the schema
SEVERITIES = ("blocker", "should-fix", "note")
_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
_CONSENSUS_ORDER = ("single_reviewer", "confirmed", "unconfirmed", "single_sighting")


def effective_severity(item: dict) -> str:
    if item["category"] in HEAVY and item.get("evidence", "").strip():
        return item["severity"]
    return "note"


def sighting(relative: str, text: str, line: int, item: dict) -> dict:
    """What submit stores for one reported finding, address included."""
    located = address.locate(relative, text, line, item.get("symbol"))
    return {
        "category": item["category"],
        "proposed": item["severity"],
        "severity": effective_severity(item),
        "file": relative,
        "line": line,
        "symbol": (item.get("symbol") or "").strip(),
        "quote": item["quote"],
        "summary": item["summary"].strip(),
        "evidence": item.get("evidence", "").strip(),
        **located,
        "fingerprint": address.fingerprint(item["category"], located["address"]),
    }


def assess(current_round) -> list[dict]:
    """One entry per fingerprint, blocking ones first."""
    planned = len(current_round.plan["runs"])
    groups: dict[str, list[dict]] = {}
    for run in current_round.plan["runs"]:
        output = current_round.outputs.get(run["run"])
        if output is None:
            continue
        reported: dict[str, dict] = {}
        for item in output.get("findings", []):
            best = reported.get(item["fingerprint"])
            if best is None or _RANK[item["severity"]] < _RANK[best["severity"]]:
                reported[item["fingerprint"]] = item
        for fingerprint, item in reported.items():
            groups.setdefault(fingerprint, []).append(
                {"run": run["run"], "model": run["model"], **item}
            )
    entries = [_entry(sightings, planned) for sightings in groups.values()]
    entries.sort(
        key=lambda e: (
            not e["blocking"],
            _RANK[e["severity"]],
            _CONSENSUS_ORDER.index(e["consensus"]),
            e["label"],
            e["fingerprint"],
        )
    )
    return entries


def _entry(sightings: list[dict], planned: int) -> dict:
    highest = SEVERITIES[min(_RANK[s["severity"]] for s in sightings)]
    if planned == 1:
        consensus, severity = "single_reviewer", highest
    elif len(sightings) >= 2:
        consensus, severity = "confirmed", highest
    elif highest == "blocker":
        consensus, severity = "unconfirmed", "blocker"
    else:
        consensus, severity = "single_sighting", "note"
    first = sightings[0]
    return {
        "source": "reviewer",
        "fingerprint": first["fingerprint"],
        "category": first["category"],
        "severity": severity,
        "consensus": consensus,
        "blocking": severity == "blocker"
        and consensus in ("single_reviewer", "confirmed"),
        "address": first["address"],
        "label": first["label"],
        "section_hash": first["section_hash"],
        "file": first["file"],
        "line": first["line"],
        "summary": first["summary"],
        "sightings": [
            {
                key: s[key]
                for key in (
                    "run",
                    "model",
                    "proposed",
                    "severity",
                    "line",
                    "symbol",
                    "summary",
                    "evidence",
                    "quote",
                    "section_hash",
                )
            }
            for s in sightings
        ],
    }


def scope_place(contract_name: str, content: str | None) -> dict:
    """The whole scope as one place; any change in scope changes its hash."""
    return {
        "address": {"kind": "scope"},
        "label": f"scope of contract {contract_name}",
        "section_hash": content or "",
        "file": None,
        "line": None,
    }


def place_at(relative: str, text: str, line: int) -> dict:
    return {
        **address.locate(relative, text, line, None),
        "file": relative,
        "line": line,
    }


def file_place(relative: str, text: str) -> dict:
    return {
        "address": {"file": relative, "kind": "file"},
        "label": relative,
        "section_hash": address.section_hash(address.split_lines(text)),
        "file": relative,
        "line": None,
    }


PLACED = ("pattern", "references")  # check kinds whose failures carry places


def pattern_places(project, evidence: dict, whole: dict) -> list[dict]:
    """Places of a failed pattern or references criterion, read from the files
    right after the check: forbid hits and broken references at their line,
    the other rules and unreadable files as the whole file."""
    locators: dict[str, address.Locator] = {}

    def locator(relative: str) -> address.Locator:
        if relative not in locators:
            try:
                data = (project.root / relative).read_bytes()
            except OSError:
                data = b""
            text = data.decode("utf-8", errors="replace")
            locators[relative] = address.Locator(relative, text)
        return locators[relative]

    def text(relative: str) -> str:
        return locator(relative).text

    rule = evidence.get("rule")
    places, truncated = [], False
    hits = evidence.get("hits", [])
    if "broken" in evidence:  # references
        hits = evidence["broken"]
        truncated |= evidence.get("broken_count", 0) > len(hits)
    if rule == "forbid" or "broken" in evidence:
        places += [
            {**locator(h["file"]).at(h["line"]), "file": h["file"], "line": h["line"]}
            for h in hits
        ]
    elif rule in ("max_words", "max_lines"):
        places += [file_place(h["file"], text(h["file"])) for h in hits]
    if rule != "require":
        truncated |= evidence.get("hit_count", 0) > len(hits)
    missing = evidence.get("missing", [])
    places += [file_place(f, text(f)) for f in missing]
    truncated |= evidence.get("missing_count", 0) > len(missing)
    unreadable = evidence.get("unreadable", [])
    places += [file_place(u["file"], "") for u in unreadable]
    truncated |= evidence.get("unreadable_count", 0) > len(unreadable)
    if truncated or not places:
        places.append(whole)
    return places


def criterion_findings(contract_name: str, criterion: dict, data: dict, whole: dict):
    """The findings of one failed criterion of a round, one per fingerprint."""
    if data["check"] == "judgment":
        places = [
            place
            for verdict in data["evidence"].get("verdicts", [])
            if verdict["passed"] is False
            for place in verdict.get("places", [])
        ]
    elif data["check"] in PLACED:
        places = data.get("places") or []
    else:
        places = []
    if not places:
        places = [whole]
    severity = "blocker" if criterion["required"] else "should-fix"
    grouped: dict[str, dict] = {}
    for place in places:
        fingerprint = address.fingerprint(
            f"criterion:{criterion['id']}", place["address"]
        )
        entry = grouped.get(fingerprint)
        if entry is None:
            grouped[fingerprint] = {
                "source": "criterion",
                "criterion": criterion["id"],
                "criterion_hash": criterion["hash"],
                "check": criterion["check"],
                "required": criterion["required"],
                "fingerprint": fingerprint,
                "category": criterion["id"],
                "severity": severity,
                "consensus": "criterion",
                "blocking": severity == "blocker",
                "address": place["address"],
                "label": place["label"],
                "section_hash": place["section_hash"],
                "file": place["file"],
                "line": place["line"],
                "lines": [place["line"]] if place["line"] else [],
                "summary": criterion["text"],
            }
        elif place["line"] and place["line"] not in entry["lines"]:
            entry["lines"].append(place["line"])
    return list(grouped.values())


DETERMINISTIC = ("command", "pattern", "references")  # checks without a model
MERGED = ("fingerprint", "category", "severity", "consensus", "summary", "line")


def merge(
    criterion_entries: list[dict], reviewer_entries: list[dict], register: set
) -> list[dict]:
    """The reviewer findings that are not at the place of a failed
    deterministic criterion (#32). One at the place of a criterion finding of
    a command, pattern or references check goes into that finding's merged
    list (the reviewers' evidence as context) instead of being a finding of
    its own; the first such criterion finding takes it. At the place: every
    sighting at one of the criterion finding's lines, or the same Markdown
    paragraph (a link may run over two lines). It stays on its own when it
    is more severe than the criterion finding (merging never lowers a
    blocker) or when either of them is in the register (register:
    fingerprints rejected or accepted when the round started; a decided
    place must not hide a new finding, a decided finding keeps its
    suppression)."""
    kept = []
    for entry in reviewer_entries:
        lines = {s.get("line") for s in entry.get("sightings") or []}
        lines = (lines | {entry.get("line")}) - {None}
        target = None
        if entry["fingerprint"] not in register:
            target = next(
                (
                    item
                    for item in criterion_entries
                    if item.get("check") in DETERMINISTIC
                    and item["fingerprint"] not in register
                    and _at(item, entry, lines)
                    and _RANK[entry["severity"]] >= _RANK[item["severity"]]
                ),
                None,
            )
        if target is None:
            kept.append(entry)
            continue
        target.setdefault("merged", []).append(
            {key: entry.get(key) for key in MERGED}
            | {"sightings": entry.get("sightings") or []}
        )
    return kept


def _at(item: dict, entry: dict, lines: set) -> bool:
    if item.get("file") is None or item["file"] != entry.get("file"):
        return False
    if lines and lines <= set(item.get("lines") or [item.get("line")]):
        return True
    where = item.get("address") or {}
    return where.get("kind") == "markdown" and where == entry.get("address")
