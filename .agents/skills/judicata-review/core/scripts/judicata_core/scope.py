"""The files a round looks at: project files matching the contract's path patterns.

Patterns are relative to the project root and use glob syntax: '*' and '?' stay
within one path segment, '**' spans any number of segments, '[...]' is a
character class. A pattern that names a directory covers everything below it
('src' is the same as 'src/**'). In a Git work tree the candidates are the
tracked and untracked but not ignored files (git ls-files), otherwise every file
below the root. .git/ and .judicata/ are never in scope, nor are symlinks.
"""

import os
import re
import subprocess
from collections.abc import Callable
from functools import cached_property

from .project import STATE_DIR, Project

EXCLUDED_DIRS = (".git", STATE_DIR)


class Scope:
    def __init__(
        self,
        project: Project,
        patterns: list[str],
        keep: Callable[[str], bool] | None = None,
    ):
        self.project = project
        self.patterns = list(patterns)
        self.keep = keep  # drops matching files too (assignment to another contract)

    @cached_property
    def files(self) -> list[str]:
        """Matching files as sorted POSIX paths relative to the project root."""
        covers = matcher(self.patterns)
        return sorted(
            path
            for path in candidates(self.project)
            if covers(path) and (self.keep is None or self.keep(path))
        )


def matcher(patterns: list[str]) -> Callable[[str], bool]:
    """A test whether a relative POSIX path is covered by one of the patterns."""
    regexes = [_glob_regex(p) for p in patterns]
    return lambda path: any(_matches(regex, path) for regex in regexes)


def candidates(project: Project) -> set[str]:
    """Every file a scope can contain (see module doc)."""
    paths = _git_files(project)
    if paths is None:
        paths = _walk(project)
    root = project.root
    return {
        path
        for path in paths
        if path.split("/", 1)[0] not in EXCLUDED_DIRS
        and not (root / path).is_symlink()
        and (root / path).is_file()
    }


def _git_files(project: Project) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=project.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None  # not a Git work tree (or no git at all)
    return [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _walk(project: Project) -> list[str]:
    found = []
    for directory, dirs, files in os.walk(project.root):
        relative = os.path.relpath(directory, project.root)
        if relative == ".":
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            prefix = ""
        else:
            dirs[:] = [d for d in dirs if d != ".git"]
            prefix = relative.replace(os.sep, "/") + "/"
        found.extend(prefix + name for name in files)
    return found


def _matches(regex: re.Pattern, path: str) -> bool:
    """The path itself or one of its parent directories matches."""
    parts = path.split("/")
    return any(
        regex.fullmatch("/".join(parts[:end])) for end in range(len(parts), 0, -1)
    )


def check_pattern(pattern: str) -> str | None:
    """None if the path pattern is usable, else what is wrong with it."""
    try:
        _glob_regex(pattern)
    except re.error as exc:
        return f"invalid path pattern: {exc}"
    return None


def _glob_regex(pattern: str) -> re.Pattern:
    """'.', './' and '/' stand for the project root; empty and '.' segments vanish."""
    segments = [s for s in pattern.strip().split("/") if s not in ("", ".")]
    if not segments:
        return re.compile(".*")
    out = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            out.append(_segment_regex(segment) + ("" if last else "/"))
    return re.compile("".join(out))


def _segment_regex(segment: str) -> str:
    out, i = [], 0
    while i < len(segment):
        char = segment[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[" and (end := _class_end(segment, i)) is not None:
            body = segment[i + 1 : end]
            negate = body[:1] in ("!", "^")
            if negate:
                body = body[1:]
            body = body.replace("\\", "\\\\").replace("[", "\\[").replace("/", "")
            out.append(f"[{'^/' if negate else ''}{body}]")
            i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def _class_end(segment: str, start: int) -> int | None:
    """Index of the ']' closing the class at start, or None (then '[' is literal)."""
    i = start + 1
    if i < len(segment) and segment[i] in "!^":
        i += 1
    if i < len(segment) and segment[i] == "]":
        i += 1
    end = segment.find("]", i)
    return end if end != -1 else None
