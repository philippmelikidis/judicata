"""Local approvals: the user's consent to run the commands of a contract.

A contract comes with the repository, so anything checked in (the contract, a
ledger event) could have been written by whoever wrote the commands. The
approval therefore lives outside the project, in the user's own state
directory, and nothing in the project can grant it:

  <state dir>/approvals/<key>.json   {project, contract, commands_hash, commands, reason, ts}

key = sha256 of the canonical project path, the contract name and the hash of
its commands. Only the command strings count, in the order they run: rewording
a criterion, its timeout or its required flag keeps the approval; a changed,
added, removed, repeated or reordered command needs a new one. The state directory is, first match wins:
--state-dir, JUDICATA_STATE_DIR, CLAUDE_PLUGIN_DATA (the plugin's data
directory, the skills pass it), $XDG_STATE_HOME/judicata,
~/.local/state/judicata. Empty values are skipped. A state directory inside
the project is refused.
"""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import contract, untrusted
from .errors import JudicataError
from .project import Project

PENDING_REASON = "waiting for approval of the contract's commands"


def state_dir(project: Project, option: str | None) -> Path:
    for value in (
        option,
        os.environ.get("JUDICATA_STATE_DIR"),
        os.environ.get("CLAUDE_PLUGIN_DATA"),
    ):
        if value:
            found = Path(value).expanduser()
            break
    else:
        xdg = os.environ.get("XDG_STATE_HOME", "")
        try:
            base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local/state"
        except RuntimeError:
            raise JudicataError(
                "no_state_dir",
                "cannot find a directory for local approvals, pass --state-dir",
            ) from None
        found = base / "judicata"
    found = found.resolve()
    if found.is_relative_to(project.root):
        raise JudicataError(
            "state_dir_in_project",
            "local approvals must live outside the project, "
            "or the repository could approve its own commands",
            {"state_dir": str(found), "project": str(project.root)},
        )
    return found


def commands(current: contract.Contract) -> list[dict]:
    return [
        {
            "criterion": c.id,
            # exactly what runs; hidden characters visible as escapes (untrusted.py)
            "command": untrusted.visible(c.params["command"]),
            "timeout": c.params["timeout"],
        }
        for c in current.criteria
        if c.check == "command"
    ]


def commands_hash(current: contract.Contract) -> str:
    # In the order they run, repeats included: both change what is executed.
    ordered = [c.params["command"] for c in current.criteria if c.check == "command"]
    text = json.dumps(ordered, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def status(project: Project, current: contract.Contract, option: str | None) -> dict:
    """Whether this user approved the current commands of the contract here.

    A contract without commands needs no approval (and no state directory).
    """
    listed = commands(current)
    digest = commands_hash(current)
    approved = not listed or (
        _record(project, current.name, digest, state_dir(project, option)) is not None
    )
    return {
        "contract": current.name,
        "approved": approved,
        "commands": listed,
        "commands_hash": digest,
    }


def approve(
    project: Project, name: str | None, given_hash: str, option: str | None
) -> dict:
    """contract approve: approve exactly the commands the user was shown."""
    current = contract.select(project, name)
    digest = commands_hash(current)
    if given_hash != digest:
        raise JudicataError(
            "approval_mismatch",
            f"the commands of contract {current.name!r} are not the ones shown, "
            "show them to the user again",
            {
                "contract": current.name,
                "commands_hash": digest,
                "commands": commands(current),
            },
        )
    path = grant(project, current, option, "user")
    return {"approval": status(project, current, option), "file": str(path)}


def grant(
    project: Project, current: contract.Contract, option: str | None, reason: str
) -> Path:
    """Store the approval of the current commands; returns the file."""
    digest = commands_hash(current)
    directory = state_dir(project, option)
    path = _path(project, current.name, digest, directory)
    record = {
        "project": str(project.root),
        "contract": current.name,
        "commands_hash": digest,
        "commands": commands(current),
        "reason": reason,
        "ts": datetime.now(UTC).isoformat(timespec="seconds")[:-6] + "Z",
    }
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except BaseException as exc:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if not isinstance(exc, OSError):
            raise
        # A sandbox (Codex) may allow writing only inside the workspace.
        raise JudicataError(
            "state_dir_not_writable",
            "cannot store the approval in the local state directory "
            f"({exc.strerror or type(exc).__name__}); allow writing there "
            "or pass --state-dir with a writable directory outside the project",
            {"state_dir": str(directory)},
        ) from None
    return path


def _path(project: Project, name: str, digest: str, directory: Path) -> Path:
    key = json.dumps([str(project.root), name, digest], ensure_ascii=False)
    return (
        directory
        / "approvals"
        / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"
    )


def _record(project: Project, name: str, digest: str, directory: Path) -> dict | None:
    """The stored approval, or None. Anything unreadable counts as not approved."""
    path = _path(project, name, digest, directory)
    try:
        if path.resolve().is_relative_to(project.root):
            return None  # a link into the repository is no local approval
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    expected = {"project": str(project.root), "contract": name, "commands_hash": digest}
    if any(record.get(k) != v for k, v in expected.items()):
        return None
    return record
