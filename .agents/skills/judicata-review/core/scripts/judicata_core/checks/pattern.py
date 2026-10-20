"""Pattern: exactly one rule over the content of the files in scope.

forbid = "<regex>", require = "<regex>", max_words = N or max_lines = N, plus
timeout (seconds, default 60). Expressions use Python syntax with re.MULTILINE
('^' and '$' at every line). Every rule applies to each file on its own:
  forbid     fails on any match; hits = the matches (file, line, column, text)
  require    fails if a file has no match; missing = those files, hits = first match
  max_words  fails if a file has more words; hits = file with count and the line
             of the first word over the limit
  max_lines  same with lines (empty lines count, a final newline does not)
Words are whitespace-separated tokens with at least one letter or digit. Files
are read as UTF-8 (BOM dropped, \\r\\n and \\r become \\n). Binary files (a NUL
byte) are skipped; a file that cannot be read as UTF-8 fails the criterion, as
does a scope without any text file, so nothing passes unchecked.

The evaluation runs in a child process and is stopped after timeout seconds,
so a slow expression (catastrophic backtracking) cannot hang the round.
"""

import math
import multiprocessing
import re
import traceback
from pathlib import Path

from ..errors import JudicataError

RULES = ("forbid", "require", "max_words", "max_lines")
DEFAULT_TIMEOUT = 60
MAX_HITS = 50  # every list in the evidence keeps this many, <list>_count has all
MAX_TEXT = 200  # characters of a line kept as evidence
WORD_RE = re.compile(r"\S+")


def validate(params: dict) -> tuple[dict, list[tuple[str, str]]]:
    known = {*RULES, "timeout"}
    problems = [(key, "unknown field") for key in sorted(set(params) - known)]
    timeout = params.get("timeout", DEFAULT_TIMEOUT)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        problems.append(("timeout", "must be a positive number of seconds"))
    rules = [key for key in RULES if key in params]
    if len(rules) != 1:
        problems.append(("check", f"pattern needs exactly one of {', '.join(RULES)}"))
        return {}, problems
    rule = rules[0]
    value = params[rule]
    if rule in ("forbid", "require"):
        if not isinstance(value, str) or not value:
            problems.append((rule, "must be a non-empty regular expression"))
        else:
            try:
                re.compile(value, re.MULTILINE)
            except re.error as exc:
                problems.append((rule, f"invalid regular expression: {exc}"))
    elif not isinstance(value, int) or isinstance(value, bool) or value < 1:
        problems.append((rule, "must be a whole number of at least 1"))
    return {rule: value, "timeout": timeout}, problems


def run(project, criterion, scope) -> dict:
    params = criterion.params
    rule = next(key for key in RULES if key in params)
    value, timeout = params[rule], params["timeout"]
    if not scope.files:
        return _failed(
            rule, "no files in scope, check the paths of the contract", scope.patterns
        )
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(
        target=_worker,
        args=(sender, str(project.root), scope.files, rule, value),
        daemon=True,
    )
    worker.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            return _failed(
                rule,
                f"timed out after {timeout} s, the expression is too slow for "
                "these files",
                scope.patterns,
                timed_out=True,
            )
        status, payload = receiver.recv()
    except EOFError:
        status, payload = "error", "the pattern check process ended without a result"
    finally:
        receiver.close()
        if worker.is_alive():
            worker.kill()
        worker.join()
    if status == "error":
        raise JudicataError(
            "internal_error", f"pattern check failed: {payload}", {"rule": rule}
        )
    return payload


def _failed(rule: str, problem: str, patterns, timed_out: bool = False) -> dict:
    return {
        "passed": False,
        "evidence": {
            "rule": rule,
            "files_checked": 0,
            "timed_out": timed_out,
            "problem": problem,
            "paths": list(patterns),
        },
    }


def _worker(sender, root: str, files: list[str], rule: str, value) -> None:
    try:
        result = ("ok", _evaluate(Path(root), files, rule, value))
    except Exception:  # noqa: BLE001 (reported to the parent as internal_error)
        result = ("error", traceback.format_exc())
    sender.send(result)
    sender.close()


def _evaluate(root: Path, files: list[str], rule: str, value) -> dict:
    regex = re.compile(value, re.MULTILINE) if rule in ("forbid", "require") else None
    found, missing, skipped, unreadable = _Found(), _Found(), _Found(), _Found()
    checked = 0
    for path in files:
        text, reason = _read(root / path)
        if reason == "binary":
            skipped.add({"file": path, "reason": reason})
            continue
        if reason is not None:
            unreadable.add({"file": path, "reason": reason})
            continue
        checked += 1
        if rule == "forbid":
            for match in regex.finditer(text):
                if found.keep():
                    found.items.append(_hit(path, text, match.start()))
        elif rule == "require":
            match = regex.search(text)
            if match is None:
                missing.add(path)
            elif found.keep():
                found.items.append(_hit(path, text, match.start()))
        elif rule == "max_words":
            count, offset = _words(text, value)
            if count > value and found.keep():
                found.items.append({**_hit(path, text, offset), "count": count})
        else:
            count, offset = _lines(text, value)
            if count > value and found.keep():
                found.items.append({**_hit(path, text, offset), "count": count})
    evidence = {"rule": rule, "files_checked": checked, "timed_out": False}
    evidence |= {"pattern": value} if regex else {"limit": value}
    evidence |= found.as_evidence("hits", "hit_count")
    if rule == "require":
        evidence |= missing.as_evidence("missing", "missing_count")
    evidence |= skipped.as_evidence("skipped", "skipped_count")
    evidence |= unreadable.as_evidence("unreadable", "unreadable_count")
    passed = missing.count == 0 if rule == "require" else found.count == 0
    if unreadable.count:
        passed = False
        evidence["problem"] = (
            f"{unreadable.count} file(s) in scope could not be read, "
            "so the rule could not be checked on them"
        )
    elif checked == 0:
        passed = False
        evidence["problem"] = "no text files in scope, check the paths of the contract"
    return {"passed": passed, "evidence": evidence}


class _Found:
    """Counts everything but keeps only the first MAX_HITS entries."""

    def __init__(self):
        self.items, self.count = [], 0

    def keep(self) -> bool:
        """Count one more entry; True if it is to be stored in items."""
        self.count += 1
        return len(self.items) < MAX_HITS

    def add(self, item) -> None:
        if self.keep():
            self.items.append(item)

    def as_evidence(self, name: str, count_name: str) -> dict:
        return {name: self.items, count_name: self.count}


def _read(path: Path) -> tuple[str, str | None]:
    """The text with newlines normalized to \\n, or why it cannot be checked."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", f"unreadable: {exc.strerror or exc}"
    if b"\0" in data:
        return "", "binary"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "", "not UTF-8"
    return text.replace("\r\n", "\n").replace("\r", "\n"), None


def _words(text: str, limit: int) -> tuple[int, int]:
    """Number of words and the offset of the first word over the limit."""
    count, offset = 0, 0
    for match in WORD_RE.finditer(text):
        if any(char.isalnum() for char in match.group()):
            count += 1
            if count == limit + 1:
                offset = match.start()
    return count, offset


def _lines(text: str, limit: int) -> tuple[int, int]:
    """Number of lines and the offset of the first line over the limit."""
    count = text.count("\n") + (0 if text == "" or text.endswith("\n") else 1)
    offset = 0
    if count > limit:
        for _ in range(limit):
            offset = text.index("\n", offset) + 1
    return count, offset


def _hit(path: str, text: str, offset: int) -> dict:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    line = text[start : end if end != -1 else len(text)]
    return {
        "file": path,
        "line": text.count("\n", 0, offset) + 1,
        "column": offset - start + 1,
        "text": line[:MAX_TEXT],
    }
