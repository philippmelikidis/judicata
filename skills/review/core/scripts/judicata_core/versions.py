"""Contract versions: validate a proposal, store it, change it, show it.

A proposal is JSON in the shape of the TOML file (name, template, paths,
reviewers, stop, criteria with the check parameters flat in each criterion).
Storing writes .judicata/contracts/<name>.toml with every default spelled out
and appends to the ledger:
  contract_version  contract, version, contract_hash, template, paths, reviewers,
                    stop, criteria [{id, text, check, required, params, hash}]
  approval_logged   contract, version, contract_hash, reason ("created",
                    "changed")
Every stored version gets a contract_version event (version counts per contract
name, the next one is the highest plus one). A contract the user created in the
interview is approved by its author at once: create stores the local approval
of its commands (approvals.py). approval_logged is only a note in the protocol
and never approves anything; the core does not read it, since whoever checks in
a contract could check in the event as well.

change takes a proposal (or, with from_file, the contract file as edited by
hand) and compares it with the latest recorded version. Without confirm it
writes nothing and returns the difference and change_hash; with confirm equal
to change_hash it rewrites the TOML, appends the next contract_version and,
if the commands changed, approves them for this user (they confirmed the
difference, commands included; commands they never approved and that did not
change stay unapproved). The new version closes the criterion findings of changed or removed
criteria and drops their decisions (states.py); open rounds of the contract
started before it refuse to go on (contract_changed, rounds.py).
"""

import hashlib
import json
import math
import os
import tempfile
import tomllib
from pathlib import Path

from . import approvals, contract, ledger, rounds, states, untrusted
from .errors import JudicataError
from .project import Project

CONTRACT_VERSION = states.CONTRACT_VERSION
APPROVAL_LOGGED = "approval_logged"
HEADER = (
    "# judicata contract. It is frozen: change it only through /judicata:contract,\n"
    "# every stored version is recorded in .judicata/ledger.jsonl.\n"
)


def validate(project: Project, raw) -> dict:
    """Validate a proposal without writing anything."""
    current = _parse(project, raw)
    return {
        "contract": contract.to_json(current),
        "exists": (project.contracts_dir / f"{current.name}.toml").exists(),
    }


def create(project: Project, raw, state_dir: str | None = None) -> dict:
    """Store a new contract as its first version (or the next, see module doc)."""
    current = _parse(project, raw)
    path = project.contracts_dir / f"{current.name}.toml"
    if path.exists():
        raise JudicataError(
            "contract_exists",
            f"a contract named {current.name!r} already exists; "
            "contracts are frozen once stored",
            {"contract": current.name, "file": str(path)},
        )
    text = _toml_text(current, path)
    version = _next(versions(ledger.read(project), current.name))
    # For a contract with commands this resolves the state directory, so a bad
    # one fails before anything is written. An existing approval is kept.
    needs_approval = not approvals.status(project, current, state_dir)["approved"]
    project.prepare_for_writing()
    project.contracts_dir.mkdir(exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        raise JudicataError(
            "contract_exists",
            f"a contract named {current.name!r} already exists",
            {"contract": current.name, "file": str(path)},
        ) from None
    approval_file = None
    try:
        if needs_approval:
            approval_file = approvals.grant(project, current, state_dir, "created")
        ledger.append_many(
            project,
            [
                (
                    CONTRACT_VERSION,
                    {"contract": current.name, "version": version, **_body(current)},
                ),
                (
                    APPROVAL_LOGGED,
                    {
                        "contract": current.name,
                        "version": version,
                        "contract_hash": current.hash,
                        "reason": "created",
                    },
                ),
            ],
        )
    except BaseException:
        path.unlink(missing_ok=True)  # no contract without its recorded version
        if approval_file is not None:
            approval_file.unlink(missing_ok=True)
        raise
    return {
        "contract": contract.to_json(current),
        "version": version,
        "file": str(path),
        "approved": True,
    }


def change(
    project: Project,
    raw,
    name: str | None,
    from_file: bool,
    confirm: str | None,
    state_dir: str | None = None,
) -> dict:
    """Compare a proposal with the latest version; store it once confirmed."""
    if from_file:
        proposed = contract.select(project, name)
    else:
        proposed = _parse(project, _restored(project, raw))
        if name is not None and name != proposed.name:
            raise JudicataError(
                "usage",
                f"--contract {name!r} does not match the proposal {proposed.name!r}",
                {"contract": name, "proposal": proposed.name},
            )
        available = contract.names(project)
        if proposed.name not in available:
            raise JudicataError(
                "contract_not_found",
                f"no contract named {proposed.name!r} to change, "
                "create it with contract create",
                {"contract": proposed.name, "contracts": available},
            )
    path = project.contracts_dir / f"{proposed.name}.toml"
    events = ledger.read(project)
    known = versions(events, proposed.name)
    latest = known[-1] if known else None
    if latest is not None and latest["contract_hash"] == proposed.hash:
        raise JudicataError(
            "no_change",
            f"the proposal is the same as version {latest['version']} "
            f"of contract {proposed.name!r}",
            {"contract": proposed.name, "version": latest["version"]},
        )
    digest = _change_hash(proposed.name, latest, proposed)
    result = {
        "contract": _shown(proposed),
        "file": str(path),
        "file_status": _file_status(path, latest),
        "from_version": latest["version"] if latest else None,
        "version": _next(known),
        "diff": _shown_diff(proposed.name, diff(latest, contract.to_json(proposed))),
        "findings": _effect(events, proposed),
        "approval": {
            **approvals.status(project, proposed, state_dir),
            "commands_changed": _commands(latest) != _commands_of(proposed),
        },
        "change_hash": digest,
        "stored": False,
        "warnings": untrusted.contract(proposed),  # possible prompt injection
    }
    for item in latest["criteria"] if latest else []:  # the recorded version's text
        result["warnings"] += [
            w
            for w in untrusted.criterion(proposed.name, item)
            if w not in result["warnings"]
        ]
    if confirm is None:
        return result
    if confirm != digest:
        raise JudicataError(
            "change_mismatch",
            f"contract {proposed.name!r} or the proposal is not the one shown, "
            "show the difference to the user again",
            {
                "contract": proposed.name,
                "change_hash": digest,
                "from_version": result["from_version"],
            },
        )
    _store(project, proposed, path, result, state_dir)
    result["approval"] = {
        **approvals.status(project, proposed, state_dir),
        "commands_changed": result["approval"]["commands_changed"],
    }
    result["stored"] = True
    return result


def _store(project, proposed, path: Path, result: dict, state_dir) -> None:
    text = _toml_text(proposed, path)
    project.prepare_for_writing()
    previous = path.read_bytes() if path.is_file() else None
    approval_file = None
    _write(path, text)
    try:
        # The user confirmed new commands in the difference: approved as their
        # own. Unchanged commands they never approved stay unapproved.
        approval = result["approval"]
        if approval["commands_changed"] and not approval["approved"]:
            approval_file = approvals.grant(project, proposed, state_dir, "changed")
        version = result["version"]
        ledger.append_many(
            project,
            [
                (
                    CONTRACT_VERSION,
                    {"contract": proposed.name, "version": version, **_body(proposed)},
                ),
                (
                    APPROVAL_LOGGED,
                    {
                        "contract": proposed.name,
                        "version": version,
                        "contract_hash": proposed.hash,
                        "reason": "changed",
                    },
                ),
            ],
        )
    except BaseException:
        if previous is not None:  # no new file without its recorded version
            _write(path, previous.decode("utf-8"))
        if approval_file is not None:
            approval_file.unlink(missing_ok=True)
        raise


def diff(old: dict | None, new: dict) -> dict:
    """What changed from a recorded version (None: nothing recorded) to new."""

    def pair(key):
        before = None if old is None else old.get(key)
        if old is not None and key in contract.ADDED_IN_0_2:
            # a version recorded by 0.1 has the defaults of the fields added later
            before = {**contract.ADDED_IN_0_2[key], **(before or {})}
        after = new[key]
        return None if before == after else {"from": before, "to": after}

    paths = pair("paths")
    if paths is not None:
        before = paths["from"] or []
        paths["added"] = [p for p in paths["to"] if p not in before]
        paths["removed"] = [p for p in before if p not in paths["to"]]
    old_criteria = {c["id"]: c for c in (old or {}).get("criteria", [])}
    new_ids = [c["id"] for c in new["criteria"]]
    added, changed, unchanged = [], [], []
    for item in new["criteria"]:
        before = old_criteria.get(item["id"])
        if before is None:
            added.append(item)
        elif before["hash"] == item["hash"]:
            unchanged.append(item["id"])
        else:
            changed.append(
                {
                    "id": item["id"],
                    "changes": _criterion_changes(before, item),
                    "from": before,
                    "to": item,
                }
            )
    kept_old = [i for i in old_criteria if i in new_ids]
    kept_new = [i for i in new_ids if i in old_criteria]
    return {
        "template": pair("template"),
        "paths": paths,
        "reviewers": pair("reviewers"),
        "stop": pair("stop"),
        "verifier": pair("verifier"),
        "criteria": {
            "added": added,
            "removed": [c for i, c in old_criteria.items() if i not in new_ids],
            "changed": changed,
            "unchanged": unchanged,
            "order": None
            if kept_old == kept_new
            else {"from": kept_old, "to": kept_new},
        },
    }


def _shown_diff(name: str, found: dict) -> dict:
    """The difference for output: flagged criterion texts only cleaned."""
    criteria = found["criteria"]
    for key in ("added", "removed"):
        criteria[key] = [untrusted.criterion_text(name, c) for c in criteria[key]]
    for entry in criteria["changed"]:
        entry["from"] = untrusted.criterion_text(name, entry["from"])
        entry["to"] = untrusted.criterion_text(name, entry["to"])
        for change in entry["changes"]:
            if change["field"] == "text":
                for side in ("from", "to"):
                    shown = untrusted.criterion_text(name, {"text": change[side]})
                    change[side] = shown["text"]
    return found


def _criterion_changes(before: dict, after: dict) -> list[dict]:
    found = []
    for key in ("text", "check", "required"):
        if before.get(key) != after[key]:
            found.append({"field": key, "from": before.get(key), "to": after[key]})
    old_params, new_params = before.get("params", {}), after["params"]
    for key in [*new_params, *(k for k in old_params if k not in new_params)]:
        if old_params.get(key) != new_params.get(key):
            found.append(
                {"field": key, "from": old_params.get(key), "to": new_params.get(key)}
            )
    return found


def _effect(events: list[dict], proposed: contract.Contract) -> dict:
    """Criterion findings the new version closes (with their decisions), and
    how many decided findings keep their decision."""
    found = states.replay(events, rounds.replay(events))
    hashes = {c.id: c.hash for c in proposed.criteria}
    lapse, kept = [], 0
    for item in states.of_contract(found, proposed.name):
        why = states.lapse_reason(item, hashes)
        if why is not None:
            lapse.append(
                untrusted.shown(  # flagged text only cleaned
                    {
                        "fingerprint": item.fingerprint,
                        "label": item.info.get("label"),
                        "criterion": item.info.get("criterion"),
                        "state": item.state,
                        "decision": states.public_decision(item.decision),
                        "why": why,
                        "warnings": item.warnings(),
                    }
                )
            )
        elif item.decision is not None and item.state != "closed":
            kept += 1
    return {"lapse": lapse, "decisions_kept": kept}


def _commands(version: dict | None) -> list[str]:
    if version is None:
        return []
    return [
        c["params"]["command"] for c in version["criteria"] if c["check"] == "command"
    ]


def _commands_of(current: contract.Contract) -> list[str]:
    return [c.params["command"] for c in current.criteria if c.check == "command"]


def _change_hash(name: str, latest: dict | None, proposed: contract.Contract) -> str:
    # the version number too: a change and its revert give the same hash again
    key = [
        name,
        latest["version"] if latest else None,
        latest["contract_hash"] if latest else None,
        proposed.hash,
    ]
    text = json.dumps(key, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_status(path: Path, latest: dict | None) -> str:
    """The contract file against the latest version: recorded, changed,
    unrecorded (no version yet) or invalid (cannot be read as a contract)."""
    try:
        on_disk = contract.load(path)
    except (JudicataError, OSError):
        return "invalid"
    if latest is None:
        return "unrecorded"
    return "recorded" if latest["contract_hash"] == on_disk.hash else "changed"


def _next(known: list[dict]) -> int:
    # the highest plus one: two branches may both have stored a version 2
    return max((v["version"] for v in known), default=0) + 1


def show(project: Project, name: str | None, state_dir: str | None = None) -> dict:
    """The contract as stored, and whether the ledger knows this version.

    status: recorded (matches the latest version), changed (the file differs
    from the latest version, edited by hand), unrecorded (no version yet).
    approval: whether this user approved its commands here (approvals.status).
    """
    current = contract.select(project, name)
    known = versions(ledger.read(project), current.name)
    if not known:
        status, version = "unrecorded", None
    elif known[-1]["contract_hash"] == current.hash:
        status, version = "recorded", known[-1]["version"]
    else:
        status, version = "changed", None
    return {
        "contract": _shown(current),
        "file": current.file,
        "version": version,
        "latest_version": known[-1]["version"] if known else None,
        "status": status,
        "approval": approvals.status(project, current, state_dir),
        "warnings": untrusted.contract(current),  # possible prompt injection
    }


def _restored(project: Project, raw):
    """A proposal built from contract show: a criterion text or parameter that
    show cleaned (untrusted.criterion_text) and that comes back unchanged means
    the stored value, not a change to the cleaned one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("criteria"), list):
        return raw
    try:
        current = contract.select(project, raw.get("name"))
    except JudicataError:
        return raw
    restored = dict(raw)
    restored["criteria"] = []
    for item in raw["criteria"]:
        old = current.criterion(item.get("id")) if isinstance(item, dict) else None
        if old is None:
            restored["criteria"].append(item)
            continue
        exact = contract.criterion_json(old)
        shown = untrusted.criterion_text(current.name, exact)
        item = dict(item)
        if item.get("text") == shown["text"] != exact["text"]:
            item["text"] = exact["text"]
        for key, value in exact["params"].items():
            if item.get(key) == shown["params"][key] != value:
                item[key] = value
        restored["criteria"].append(item)
    return restored


def _shown(current: contract.Contract) -> dict:
    """The contract for output: flagged criterion texts only cleaned (warnings
    name them; commands stay exact, they are shown for approval)."""
    shown = contract.to_json(current)
    shown["criteria"] = [
        untrusted.criterion_text(current.name, c) for c in shown["criteria"]
    ]
    return shown


def versions(events: list[dict], name: str) -> list[dict]:
    """The recorded versions of one contract, oldest first."""
    found = []
    for event in events:
        if event["type"] != CONTRACT_VERSION:
            continue
        data = event["data"]
        if data.get("contract") != name:
            continue
        if not isinstance(data.get("version"), int) or "contract_hash" not in data:
            raise JudicataError(
                "ledger_corrupt",
                f"ledger event {event['id']} ({event['type']}) is incomplete",
                {"event": event["id"], "type": event["type"]},
            )
        found.append(data)
    return found


def _parse(project: Project, raw) -> contract.Contract:
    if not isinstance(raw, dict):
        raise JudicataError(
            "invalid_input",
            "the contract must be a JSON object",
            {"got": type(raw).__name__},
        )
    name = raw.get("name")
    stem = name if isinstance(name, str) and contract.NAME_RE.match(name) else "_"
    path = project.contracts_dir / f"{stem}.toml"
    _check_writable(raw, path)
    return contract.parse(raw, path)


def _body(current: contract.Contract) -> dict:
    data = contract.to_json(current)
    return {
        "contract_hash": data.pop("hash"),
        **{k: v for k, v in data.items() if k != "name"},
    }


def _toml_text(current: contract.Contract, path: Path) -> str:
    text = to_toml(current)
    stored = contract.parse(tomllib.loads(text), path)
    if stored.hash != current.hash:  # the writer must round-trip exactly
        raise JudicataError(
            "internal_error",
            "the contract does not survive writing as TOML",
            {"contract": current.name},
        )
    return text


def _write(path: Path, text: str) -> None:
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def to_toml(current: contract.Contract) -> str:
    lines = [HEADER.rstrip("\n")]
    lines += [
        f"name = {_value(current.name)}",
        f"template = {_value(current.template)}",
        f"paths = {_value(current.paths)}",
        "",
        "[reviewers]",
        f"count = {_value(current.reviewers['count'])}",
        f"models = {_value(current.reviewers['models'])}",
        "",
        "[stop]",
        *(f"{key} = {_value(value)}" for key, value in current.stop.items()),
        "",
        "[verifier]",
        *(f"{key} = {_value(value)}" for key, value in current.verifier.items()),
    ]
    for criterion in current.criteria:
        lines += [
            "",
            "[[criteria]]",
            f"id = {_value(criterion.id)}",
            f"text = {_value(criterion.text)}",
            f"check = {_value(criterion.check)}",
            f"required = {_value(criterion.required)}",
            *(f"{key} = {_value(value)}" for key, value in criterion.params.items()),
        ]
    return "\n".join(lines) + "\n"


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} as TOML")


def _string(text: str) -> str:
    out = ['"']
    for char in text:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _check_writable(raw: dict, path) -> None:
    """Reject what TOML cannot hold before parsing (lone surrogates, huge numbers)."""
    problems = []

    def visit(where: str, value) -> None:
        if isinstance(value, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                problems.append({"field": where, "problem": "contains invalid unicode"})
        elif isinstance(value, bool):
            return
        elif isinstance(value, int) and not -(2**63) <= value < 2**63:
            problems.append({"field": where, "problem": "number is too large"})
        elif isinstance(value, float) and not math.isfinite(value):
            problems.append({"field": where, "problem": "must be a finite number"})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(f"{where}[{index}]", item)
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(f"{where}.{key}" if where else str(key), item)

    visit("", raw)
    if problems:
        summary = "; ".join(f"{p['field']}: {p['problem']}" for p in problems)
        raise JudicataError(
            "invalid_contract",
            f"contract {path.name} is invalid: {summary}",
            {"file": str(path), "problems": problems},
        )
