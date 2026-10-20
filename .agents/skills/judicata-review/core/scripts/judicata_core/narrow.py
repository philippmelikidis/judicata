"""Scope and focus of one round, from what the user asked for.

Scope (round start --for PATH... and/or --diff BASE), stored in round_started:
  {kind, paths, base, merge_base, only}
  kind   contract  the contract's files (no narrowing, only = None)
         paths     the contract's files below the given paths (only = the paths)
         diff      the contract's files changed against BASE (changes.py), below
                   the given paths if there are any (only = those files, fixed
                   at round start)
The round's files are the contract's (assignment.scope) within only. A narrowed
scope without any file of the contract is refused (empty_scope). Rounds count
per contract and scope (#14): kind, paths and base name the scope, only is the
snapshot.

Focus (round start --criteria IDS --categories CATS, comma-separated, "" = none,
left out = all): the criteria the round judges and the finding categories it
records. round_started keeps only the criteria in focus and stores
focus {criteria, categories}; a focus that covers everything is no focus (None).
A focused round closes with focus_clear or focus_open, never done, and
round_closed has focused = true: it is not a round for the stop rule.
"""

from . import assignment, changes, findings
from .errors import JudicataError
from .project import Project

MAX_LISTED = 50


def scope(project: Project, paths: list[str], base: str | None) -> dict:
    given = list(
        dict.fromkeys(assignment.relative_path(project, path) for path in paths)
    )
    if "." in given:
        given = ["."]
    if base is not None:
        found = changes.changed(project, base)
        within = assignment.within(given or ["."])
        return {
            "kind": "diff",
            "paths": given,
            "base": found.base,
            "merge_base": found.merge_base,
            "only": [f for f in found.files if within(f)],
        }
    if given and given != ["."]:
        return {
            "kind": "paths",
            "paths": given,
            "base": None,
            "merge_base": None,
            "only": given,
        }
    return scope_of_contract()


def public(
    stored: dict | None, files: list[str] | None = None, limit: int | None = MAX_LISTED
) -> dict:
    """The scope as the skill sees it: without the snapshot, with the files if
    given (at most limit of them, file_count has all)."""
    stored = stored or scope_of_contract()
    shown = {key: stored[key] for key in ("kind", "paths", "base", "merge_base")}
    if files is not None:
        shown["files"] = files if limit is None else files[:limit]
        shown["file_count"] = len(files)
    return shown


def scope_of_contract() -> dict:
    return {
        "kind": "contract",
        "paths": [],
        "base": None,
        "merge_base": None,
        "only": None,
    }


def empty_scope(name: str, stored: dict) -> JudicataError:
    return JudicataError(
        "empty_scope",
        f"contract {name!r} covers none of the files in this scope",
        {
            "contract": name,
            "kind": stored["kind"],
            "paths": stored["paths"],
            "base": stored["base"],
        },
    )


def focus(
    criteria_ids: list[str], given: list[str] | None, categories: list[str] | None
) -> dict | None:
    """The focus of a round, or None when it covers the whole contract."""
    if given is None and categories is None:
        return None
    if given is not None:
        unknown = [cid for cid in given if cid not in criteria_ids]
        if unknown:
            raise JudicataError(
                "criterion_not_found",
                f"the contract has no criterion {', '.join(unknown)}",
                {"unknown": unknown, "criteria": criteria_ids},
            )
    chosen = criteria_ids if given is None else [c for c in criteria_ids if c in given]
    kinds = (
        list(findings.CATEGORIES)
        if categories is None
        else [c for c in findings.CATEGORIES if c in categories]
    )
    if not chosen and not kinds:
        raise JudicataError(
            "empty_focus",
            "the focus leaves no criterion and no finding category to review",
            {},
        )
    if chosen == criteria_ids and kinds == list(findings.CATEGORIES):
        return None
    return {"criteria": chosen, "categories": kinds}


def categories(current_focus: dict | None) -> list[str]:
    """The finding categories a round records."""
    if current_focus is None:
        return list(findings.CATEGORIES)
    return current_focus["categories"]
