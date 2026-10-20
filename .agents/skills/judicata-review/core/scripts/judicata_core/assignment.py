"""Assignment: every artifact file belongs to exactly one contract.

A file belongs to the contract whose path patterns cover it. If several cover it,
the user answers once and contract assign records the answer:
  contract_assigned  path ("." for the project root), contract, among (the
                     contracts that were in question, sorted)
An answer covers its path and everything below it, for the files covered by
exactly the contracts in among. For a file the closest such answer (the file
itself, then its directories upwards) decides; for the same path and contracts
the later answer wins. A contract added later that covers the file too makes it
a new question. Files no contract covers are unmatched: the interview makes a
contract for them (.gitattributes, which the core writes itself, only when it is
named directly). A round leaves out the files answered for another contract.
"""

from pathlib import Path

from . import contract, ledger
from .errors import JudicataError
from .project import Project
from .scope import Scope, candidates, matcher

CONTRACT_ASSIGNED = "contract_assigned"
MAX_FILES = 50  # files listed per group, file_count has all
WILDCARDS = set("*?[]!^")
OWN_FILES = {".gitattributes"}  # the core writes it; unmatched only when named


def resolve(project: Project, given: list[str], only: list[str] | None = None) -> dict:
    """Group the files of the given paths by contract, ambiguous or unmatched.

    only: just these files count (the changes of a diff).
    """
    contracts = _contracts(project)
    paths = [relative_path(project, path, must_exist=False) for path in given]
    answers = answered(ledger.read(project))
    files = candidates(project)
    only = None if only is None else set(only)
    resolved: dict[str, dict] = {}
    ambiguous: dict[tuple, dict] = {}
    unmatched: dict[str, list[str]] = {}
    no_files, not_found, seen = [], [], set()
    for path in dict.fromkeys(paths):
        if not (project.root / path).exists():
            not_found.append(path)
            continue
        below = sorted(
            f for f in files if _below(f, path) and (only is None or f in only)
        )
        if not below:
            no_files.append(path)
        for file in below:
            if file in seen:
                continue
            seen.add(file)
            covering = _covering(contracts, file)
            if not covering:
                if file not in OWN_FILES or file == path:
                    unmatched.setdefault(path, []).append(file)
                continue
            name, by = _decide(file, covering, answers)
            if name is not None:
                group = resolved.setdefault(name, {"files": [], "assigned": set()})
                group["files"].append(file)
                if by is not None:
                    group["assigned"].add(by)
                continue
            group = ambiguous.setdefault(
                (path, frozenset(covering)), {"files": [], "score": {}}
            )
            group["files"].append(file)
            for candidate, score in covering.items():
                group["score"][candidate] = group["score"].get(candidate, 0) + score
    return {
        "contracts": [c.name for c in contracts],
        "resolved": [
            {
                "contract": name,
                **_listed(group["files"]),
                "assigned": sorted(group["assigned"]),
            }
            for name, group in sorted(resolved.items())
        ],
        "ambiguous": [
            {
                "path": path,
                "contracts": sorted(
                    group["score"], key=lambda n: (-group["score"][n], n)
                ),
                **_listed(group["files"]),
            }
            for (path, _), group in ambiguous.items()
        ],
        "unmatched": [
            {"path": path, **_listed(found)} for path, found in unmatched.items()
        ],
        "no_files": no_files,
        "not_found": not_found,
        "complete": not ambiguous and not unmatched,
    }


def assign(project: Project, given: str, name: str, among: list[str]) -> dict:
    """Record the user's answer: below the path, name wins among these contracts."""
    contracts = {c.name: c for c in _contracts(project)}
    if name not in contracts:
        raise _not_found(name, contracts)
    question = set(among)
    if len(question) < 2 or name not in question:
        raise JudicataError(
            "invalid_input",
            "--among needs the contracts in question (at least two), "
            "including the one chosen with --contract",
            {"contract": name, "among": among},
        )
    for other in sorted(question):
        if other not in contracts:
            raise _not_found(other, contracts)
    path = relative_path(project, given)
    below = [f for f in candidates(project) if _below(f, path)]
    covered = [f for f in below if set(_covering(contracts.values(), f)) == question]
    if not covered:
        matching = sorted(
            other.name for other in contracts.values() if any(map(other.covers, below))
        )
        raise JudicataError(
            "contract_not_matching",
            f"no file in {path} is covered by exactly the contracts "
            f"{', '.join(sorted(question))}",
            {"contract": name, "path": path, "contracts": matching},
        )
    event = ledger.append(
        project,
        CONTRACT_ASSIGNED,
        {"path": path, "contract": name, "among": sorted(question)},
    )
    return {
        "path": path,
        "contract": name,
        "among": sorted(question),
        "file_count": len(covered),
        "event": event["id"],
    }


def scope(
    project: Project, current: contract.Contract, only: list[str] | None = None
) -> Scope:
    """The files of a round: the contract's, minus those answered for another one,
    within only (files or directories, see narrow.py) if given."""
    left_out = set(excluded(project, current))
    inside = None if only is None else within(only)
    if not left_out and inside is None:
        return Scope(project, current.paths)
    return Scope(
        project,
        current.paths,
        keep=lambda file: file not in left_out and (inside is None or inside(file)),
    )


def within(paths: list[str]):
    """A test whether a file is one of the paths or below one of them."""
    given = set(paths)
    if "." in given:
        return lambda file: True

    def inside(file: str) -> bool:
        if file in given:
            return True
        parts = file.split("/")
        return any("/".join(parts[:end]) in given for end in range(1, len(parts)))

    return inside


def excluded(project: Project, current: contract.Contract) -> list[str]:
    """Files the contract's patterns cover that the user assigned to another one."""
    answers = answered(ledger.read(project))
    if not answers:
        return []
    contracts = _contracts(project)
    out = []
    for file in Scope(project, current.paths).files:
        covering = _covering(contracts, file)
        if len(covering) < 2:
            continue
        name, _ = _decide(file, covering, answers)
        if name not in (None, current.name):
            out.append(file)
    return out


def answered(events: list[dict]) -> dict[tuple[str, frozenset], str]:
    """(path, contracts in question) -> chosen contract, the later answer winning."""
    answers = {}
    for event in events:
        if event["type"] != CONTRACT_ASSIGNED:
            continue
        data = event["data"]
        among = data.get("among")
        if (
            not isinstance(data.get("path"), str)
            or not isinstance(data.get("contract"), str)
            or not isinstance(among, list)
            or not all(isinstance(item, str) for item in among)
        ):
            raise JudicataError(
                "ledger_corrupt",
                f"ledger event {event['id']} ({event['type']}) is incomplete",
                {"event": event["id"], "type": event["type"]},
            )
        answers[(data["path"], frozenset(among))] = data["contract"]
    return answers


def relative_path(project: Project, given: str, must_exist: bool = True) -> str:
    """The path as POSIX relative to the project root, "." for the root itself."""
    path = Path(given).expanduser()
    if not path.is_absolute():
        path = project.root / path
    try:
        relative = path.resolve().relative_to(project.root).as_posix()
    except ValueError:
        raise JudicataError(
            "path_outside_project",
            f"{given} is not inside the project {project.root}",
            {"path": given, "project": str(project.root)},
        ) from None
    if must_exist and not path.exists():
        raise JudicataError(
            "path_not_found", f"{given} does not exist", {"path": given}
        )
    return relative


class _Covering:
    """A contract with a test per path pattern; covers() is 0 or the specificity."""

    def __init__(self, current: contract.Contract):
        self.name = current.name
        self.patterns = [
            (matcher([p]), sum(ch not in WILDCARDS for ch in p.strip("/ ")))
            for p in current.paths
        ]

    def covers(self, file: str) -> int:
        """1 + the literal characters of the most specific pattern covering it."""
        return max(
            (1 + score for test, score in self.patterns if test(file)), default=0
        )


def _contracts(project: Project) -> list[_Covering]:
    return [
        _Covering(contract.load(project.contracts_dir / f"{name}.toml"))
        for name in contract.names(project)
    ]


def _covering(contracts, file: str) -> dict[str, int]:
    """Name -> specificity of every contract covering the file."""
    return {c.name: score for c in contracts if (score := c.covers(file))}


def _decide(file: str, covering: dict, answers: dict) -> tuple[str | None, str | None]:
    """(contract, path of the deciding answer or None); (None, None) = ask."""
    if len(covering) == 1:
        return next(iter(covering)), None
    question = frozenset(covering)
    parts = file.split("/")
    for end in range(len(parts), -1, -1):
        path = "/".join(parts[:end]) or "."
        if (path, question) in answers:
            return answers[(path, question)], path
    return None, None


def _below(file: str, path: str) -> bool:
    return path == "." or file == path or file.startswith(path + "/")


def _listed(files: list[str]) -> dict:
    return {"files": sorted(files)[:MAX_FILES], "file_count": len(files)}


def _not_found(name: str, contracts: dict) -> JudicataError:
    return JudicataError(
        "contract_not_found",
        f"no contract named {name!r}",
        {"contract": name, "contracts": sorted(contracts)},
    )
