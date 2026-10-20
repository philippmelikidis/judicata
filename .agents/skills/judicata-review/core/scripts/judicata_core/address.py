"""Normalized address, section hash and fingerprint of a place in an artifact.

The address names a place so that it is found again after lines moved:
  python    {file, kind, symbol}            class and function around the line (ast),
                                            nested names joined by ".", the n-th
                                            definition of the same name gets "@n"
  markdown  {file, kind, headings, paragraph} heading path (casefolded, spaces
                                            collapsed) and the paragraph under the
                                            last heading (1, 2, ...; 0 = the heading);
                                            "@" in a heading is doubled, the n-th
                                            heading of the same name gets "@n"
  symbol    {file, kind, symbol}            other files: the reviewer's symbol name
  file      {file, kind}                    no section found (module level, no symbol)
Python that does not parse is treated like any other file.

The section hash is SHA-256 over the section text (line endings normalized):
the definition (decorators to last line, minus the indent of its first line) or
the paragraph. symbol and file addresses hash the whole file, so any change in the
file counts as a change of the place.

Fingerprint (stage one): SHA-256 over category and address, independent of line
numbers, run and session.
"""

import ast
import bisect
import hashlib
import json
import re

PYTHON_SUFFIXES = (".py", ".pyi")
MARKDOWN_SUFFIXES = (".md", ".markdown")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_OR_QUOTE = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)]|>)(?:[ \t]|$)")


def split_lines(text: str) -> list[str]:
    """The lines of a text, split at line breaks only (\\n, \\r\\n, \\r).

    Unlike str.splitlines, a form feed or a Unicode line separator stays inside
    its line, so line numbers match an editor, the reviewer's Read tool, Python's
    ast and the pattern check. A final line break adds no empty line.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def locate(relative: str, text: str, line: int, symbol: str | None) -> dict:
    """{address, label, section_hash} of the place at a 1-based line."""
    return Locator(relative, text).at(line, symbol)


class Locator:
    """Parses one file once and locates any number of lines in it."""

    def __init__(self, relative: str, text: str):
        self.relative, self.text = relative, text
        self.lines = split_lines(text)
        lower = relative.lower()
        self.python = lower.endswith(PYTHON_SUFFIXES)
        self.markdown = lower.endswith(MARKDOWN_SUFFIXES)
        self._index = None
        self._parsed = None

    def at(self, line: int, symbol: str | None = None) -> dict:
        relative, lines = self.relative, self.lines
        found = None
        if self.python:
            if self._index is None:
                self._index = _python_index(self.text)
            found = _python(relative, lines, self._index, line)
        elif self.markdown:
            if self._index is None:
                self._index = _markdown_index(lines)
            found = _markdown(relative, lines, self._index, line)
        if found is None:
            name = normalize_symbol(symbol)
            if name and not (self.python and self.parses()):
                address = {"file": relative, "kind": "symbol", "symbol": name}
                found = address, f"{relative}::{name}", lines
            else:
                found = {"file": relative, "kind": "file"}, relative, lines
        address, label, section = found
        return {
            "address": address,
            "label": label,
            "section_hash": section_hash(section),
        }

    def parses(self) -> bool:
        if self._parsed is None:
            self._parsed = _parses(self.text)
        return self._parsed


def fingerprint(category: str, address: dict) -> str:
    canonical = json.dumps(
        [category, address], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def section_hash(lines: list[str]) -> str:
    text = "\n".join(lines).strip("\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_symbol(symbol: str | None) -> str:
    """'Parser::parse()', ' Parser#parse ' and 'Parser.parse' are the same name."""
    if not symbol:
        return ""
    name = " ".join(symbol.split())
    name = re.sub(r"\(.*\)$", "", name).strip()
    return re.sub(r"\s*(?:::|#|->)\s*", ".", name).strip(".")


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    return True


def _python_index(text: str) -> list[tuple[int, int, str]] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    definitions: list[tuple[int, int, str]] = []  # (start, end, qualified name)
    seen: dict[str, int] = {}

    def visit(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = f"{prefix}{child.name}"
                seen[name] = seen.get(name, 0) + 1
                if seen[name] > 1:
                    name = f"{name}@{seen[name]}"
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                definitions.append((start, child.end_lineno or child.lineno, name))
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return definitions


def _python(relative: str, lines: list[str], definitions, line: int):
    if definitions is None:
        return None
    around = [d for d in definitions if d[0] <= line <= d[1]]
    if not around:
        return None
    start, end, name = max(around, key=lambda d: d[0])  # the innermost one
    first = lines[start - 1]
    indent = first[: len(first) - len(first.lstrip())]
    section = [text.removeprefix(indent) for text in lines[start - 1 : end]]
    address = {"file": relative, "kind": "python", "symbol": name}
    return address, f"{relative}::{name}", section


def _markdown_index(lines: list[str]):
    """(sections, owner, owned lines sorted). Fenced code is one paragraph."""
    sections = []  # each: {"path": [(key, title)], "heading": [text], "blocks": [[text]]}
    stack: list[tuple[int, str, str]] = []  # (level, key, title)
    counts: dict[tuple, int] = {}
    current = {"path": [], "heading": [], "blocks": []}
    sections.append(current)
    owner: dict[int, tuple[int, int]] = {}  # line number -> (section index, paragraph)
    block: list | None = None
    fence: str | None = None
    index = 0

    def open_section(level: int, title: str, heading_lines: list[int]) -> None:
        nonlocal current, block
        while stack and stack[-1][0] >= level:
            stack.pop()
        key = " ".join(title.split()).casefold().replace("@", "@@")
        parent = tuple(k for _, k, _ in stack)
        counts[(parent, key)] = counts.get((parent, key), 0) + 1
        if counts[(parent, key)] > 1:
            key = f"{key}@{counts[(parent, key)]}"
        stack.append((level, key, title.strip()))
        current = {
            "path": [(k, t) for _, k, t in stack],
            "heading": [lines[n] for n in heading_lines],
            "blocks": [],
        }
        sections.append(current)
        for n in heading_lines:
            owner[n] = (len(sections) - 1, 0)
        block = None

    def add(number: int) -> None:
        nonlocal block
        if block is None:
            block = []
            current["blocks"].append(block)
        block.append(lines[number])
        owner[number] = (len(sections) - 1, len(current["blocks"]))

    while index < len(lines):
        text = lines[index]
        if fence is not None:
            add(index)
            if text.strip().startswith(fence) and set(text.strip()) == {fence[0]}:
                fence, block = None, None
            index += 1
            continue
        opening = _FENCE.match(text)
        if opening:
            block = None
            fence = opening.group(1)
            add(index)
            index += 1
            continue
        heading = _ATX.match(text)
        if heading:
            open_section(len(heading.group(1)), heading.group(2) or "", [index])
            index += 1
            continue
        end = _setext_end(lines, index) if block is None else None
        if end is not None:
            level = 1 if lines[end].strip()[0] == "=" else 2
            title = " ".join(lines[n] for n in range(index, end))
            open_section(level, title, list(range(index, end + 1)))
            index = end + 1
            continue
        if not text.strip():
            block = None
            index += 1
            continue
        add(index)
        index += 1
    return sections, owner, sorted(owner)


def _markdown(relative: str, lines: list[str], index, line: int):
    """Heading path and paragraph of a line."""
    sections, owner, owned = index
    number = line - 1
    if number not in owner:
        # a blank line belongs to the paragraph before it; right under a heading
        # (or before any text) to the paragraph after it
        position = bisect.bisect_left(owned, number)
        before = owned[position - 1] if position > 0 else None
        after = owned[position] if position < len(owned) else None
        same = (
            after is not None
            and before is not None
            and owner[after][0] == owner[before][0]
        )
        if before is not None and (owner[before][1] != 0 or not same):
            number = before
        elif after is not None:
            number = after
        else:
            return None
    section_index, paragraph = owner[number]
    found = sections[section_index]
    text_lines = found["heading"] if paragraph == 0 else found["blocks"][paragraph - 1]
    address = {
        "file": relative,
        "kind": "markdown",
        "headings": [key for key, _ in found["path"]],
        "paragraph": paragraph,
    }
    titles = " > ".join(title for _, title in found["path"])
    label = f"{relative} > {titles}" if titles else relative
    label += f", paragraph {paragraph}" if paragraph else ", heading"
    return address, label, text_lines


def _setext_end(lines: list[str], index: int) -> int | None:
    """Index of the underline if a paragraph starting at index is a setext heading."""
    first = lines[index]
    if not first.strip() or _SETEXT.match(first) or _LIST_OR_QUOTE.match(first):
        return None
    end = index
    while end < len(lines) and lines[end].strip():
        if _SETEXT.match(lines[end]):
            return end if end > index else None
        if _ATX.match(lines[end]) or _FENCE.match(lines[end]):
            return None
        end += 1
    return None
