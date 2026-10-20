"""What the user can name as a scope besides paths: the changes against a branch, a module.

scope diff --base REF: the files that differ between the merge base of REF and
HEAD and the work tree (committed, staged, unstaged and untracked but not
ignored), renamed files under their new name, deleted files left out. Paths are
relative to the project root, which may lie below the top of the repository.
Only scope candidates count (scope.py: no .git/, no .judicata/, no symlinks).

scope find --name NAME: files and directories whose path ends in NAME, compared
by segments and case-insensitively. A file also matches without its extension;
a dotted name (core.ledger) also matches as a path (core/ledger).
"""

import subprocess
from dataclasses import dataclass

from .errors import JudicataError
from .project import Project
from .scope import EXCLUDED_DIRS, candidates

DEFAULT_BASE = "main"
MAX_LISTED = 50  # per list in the output, the counts have all
MAX_PATCH_BYTES = 200_000  # a larger patch is left out, the reviewers read the files
MAX_PATCH_FILES = 2_000  # keeps the git command line short


@dataclass(frozen=True)
class Changes:
    base: str
    merge_base: str
    files: list[str]  # sorted, scope candidates only
    renamed: list[dict]  # [{from, to}]
    deleted: list[str]
    untracked: list[str]  # of files: new and not in Git yet, so without a patch


def changed(project: Project, base: str) -> Changes:
    if (_git(project, "rev-parse", "--is-inside-work-tree") or "").strip() != "true":
        raise JudicataError(
            "not_a_git_repository",
            f"{project.root} is not inside a Git work tree, name files or "
            "directories instead of a diff",
            {"project": str(project.root)},
        )
    if _git(project, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
        heads = _git(project, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        raise JudicataError(
            "base_not_found",
            f"no branch or commit named {base!r}",
            {"base": base, "branches": sorted((heads or "").split())},
        )
    found = _git(project, "merge-base", base, "HEAD")
    if not found:
        raise JudicataError(
            "no_merge_base",
            f"HEAD and {base} have no common commit (or there is no commit yet)",
            {"base": base},
        )
    merge_base = found.strip()
    present, renamed, deleted = _name_status(project, merge_base, base)
    untracked = _untracked(project, base)
    files = sorted((present | untracked) & candidates(project))
    return Changes(
        base,
        merge_base,
        files,
        [r for r in renamed if _own(r["to"])],
        sorted(p for p in deleted if _own(p)),
        sorted(untracked & set(files)),
    )


def patch(project: Project, merge_base: str, files: list[str]) -> dict:
    """The patch of the given files against merge_base, for the reviewers.

    Renamed files bring their old name, so Git shows the rename. Untracked files
    have no patch; they are listed in untracked and are new as a whole.
    """
    wanted = set(files)
    _, renamed, _ = _name_status(project, merge_base, merge_base)
    paths = sorted(wanted | {r["from"] for r in renamed if r["to"] in wanted})
    untracked = sorted(_untracked(project, merge_base) & wanted)
    tracked = [p for p in paths if p not in untracked]
    text = ""
    if tracked and len(tracked) <= MAX_PATCH_FILES:
        text = _git(
            project,
            "--literal-pathspecs",
            "diff",
            "-M",
            "--relative",
            "--no-ext-diff",
            "--no-color",
            merge_base,
            "--",
            *tracked,
        )
        if text is None:
            raise JudicataError(
                "git_failed", f"git diff against {merge_base} failed", {}
            )
    shown = {"patch": text, "patch_omitted": None, "untracked": untracked}
    if len(tracked) > MAX_PATCH_FILES:
        shown.update(patch=None, patch_omitted=f"more than {MAX_PATCH_FILES} files")
    elif len(text.encode("utf-8", "surrogateescape")) > MAX_PATCH_BYTES:
        shown.update(patch=None, patch_omitted=f"larger than {MAX_PATCH_BYTES} bytes")
    return shown


def _name_status(project: Project, merge_base: str, base: str):
    """(present, renamed [{from, to}], deleted) of the work tree against merge_base."""
    output = _git(
        project,
        "diff",
        "--name-status",
        "-z",
        "-M",
        "--relative",
        "--no-ext-diff",
        "--no-color",
        merge_base,
        "--",
    )
    if output is None:
        raise JudicataError(
            "git_failed", f"git diff against {base} failed", {"base": base}
        )
    present, renamed, deleted = set(), [], []
    tokens = output.split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[0] in "RC":
            old, new = tokens[i + 1], tokens[i + 2]
            i += 3
            present.add(new)
            if status[0] == "R":
                renamed.append({"from": old, "to": new})
            continue
        path = tokens[i + 1]
        i += 2
        if status[0] == "D":
            deleted.append(path)
        else:
            present.add(path)
    return present, renamed, deleted


def _untracked(project: Project, base: str) -> set[str]:
    output = _git(project, "ls-files", "-z", "--others", "--exclude-standard")
    if output is None:
        raise JudicataError(
            "git_failed", f"git ls-files failed while comparing with {base}", {}
        )
    return {p for p in output.split("\0") if p}


def diff(project: Project, base: str) -> dict:
    found = changed(project, base)
    return {
        "base": found.base,
        "merge_base": found.merge_base,
        "files": found.files[:MAX_LISTED],
        "file_count": len(found.files),
        "renamed": found.renamed[:MAX_LISTED],
        "renamed_count": len(found.renamed),
        "deleted": found.deleted[:MAX_LISTED],
        "deleted_count": len(found.deleted),
    }


def find(project: Project, name: str) -> dict:
    wanted = _key(name)
    if not wanted:
        raise JudicataError("usage", "--name must not be empty", {})
    variants = {wanted}
    if "/" not in wanted and "." in wanted:
        variants.add(wanted.replace(".", "/"))
    files = candidates(project)
    directories: dict[str, int] = {}
    matches = []
    for file in files:
        parts = file.split("/")
        for end in range(1, len(parts)):
            directory = "/".join(parts[:end])
            directories[directory] = directories.get(directory, 0) + 1
        stem = parts[-1].rsplit(".", 1)[0] if "." in parts[-1][1:] else parts[-1]
        if _suffixes(parts) & variants or _suffixes(parts[:-1] + [stem]) & variants:
            matches.append({"path": file, "kind": "file", "file_count": 1})
    for directory, count in directories.items():
        if _suffixes(directory.split("/")) & variants:
            matches.append(
                {"path": directory, "kind": "directory", "file_count": count}
            )
    matches.sort(key=lambda m: m["path"])
    return {
        "name": name,
        "matches": matches[:MAX_LISTED],
        "match_count": len(matches),
    }


def _key(text: str) -> str:
    return "/".join(s for s in text.strip().casefold().split("/") if s)


def _suffixes(parts: list[str]) -> set[str]:
    lowered = [p.casefold() for p in parts]
    return {"/".join(lowered[start:]) for start in range(len(lowered))}


def _own(path: str) -> bool:
    """Not Git or judicata state."""
    return path.split("/", 1)[0] not in EXCLUDED_DIRS


def _git(project: Project, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # no git at all
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "surrogateescape")
