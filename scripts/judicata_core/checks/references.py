"""References: links, anchors, images and citation keys in scope must exist.

Read from the Markdown files in scope (.md, .markdown, .qmd, .rmd and a few
more, see MARKDOWN):
  links      [text](path#anchor), reference definitions [label]: path and
             <a href="path">: the file or directory must exist, relative to
             the linking file ('/' at the start = the project root, as on
             GitHub), spelled exactly; an anchor (also #anchor in the same
             file) must be a heading of the target Markdown file as a GitHub
             slug (repeats get -1, -2, ...) or an id/name in its HTML
  images     ![alt](path) and <img src="path">: the file must exist
  citations  [@key] (also [see @a, p. 3; -@b]) and \\cite{key}: the key must be
             an entry of a .bib file anywhere in the project
From LaTeX files (.tex, .ltx) only \\cite{key} and its variants (\\citep,
\\parencite, ...), with comments and verbatim environments left out.

External URLs (any scheme such as https: or mailto:, or starting with //) are
never checked. Code blocks, inline code and HTML comments are not read as
links. code_paths = true also checks inline code spans that are a single path
with a slash and a file extension (`scripts/build.sh`), relative to the
project root; no further exceptions, users turn it off if it misfires.

Evidence: broken [{file, line, column, kind (link|image|citation|code_path),
reference (as written), target (the resolved path or key), reason}], sorted
by place within a file, at most 50 plus broken_count. Files in scope that are
neither Markdown nor LaTeX are not read. Unreadable or non-UTF-8 files fail
the criterion, so does an empty scope; a scope without Markdown or LaTeX files
passes with a note, there is nothing to check. The check runs in a child
process with a time limit, like the pattern check.
"""

import multiprocessing
import os
import re
import traceback
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from .. import scope as scopes
from ..errors import JudicataError
from . import pattern

TIMEOUT = 60  # seconds for all files of one criterion
MAX_REFERENCE = 200  # characters of a reference kept as evidence

MARKDOWN = {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdx", ".qmd", ".rmd"}
LATEX = {".tex", ".ltx"}


def validate(params: dict) -> tuple[dict, list[tuple[str, str]]]:
    problems = [(key, "unknown field") for key in sorted(set(params) - {"code_paths"})]
    code_paths = params.get("code_paths", False)
    if not isinstance(code_paths, bool):
        problems.append(("code_paths", "must be true or false"))
    return {"code_paths": code_paths}, problems


def run(project, criterion, scope) -> dict:
    code_paths = criterion.params["code_paths"]
    if not scope.files:
        return _failed(
            code_paths, "no files in scope, check the paths of the contract", scope
        )
    bib_files = sorted(p for p in scopes.candidates(project) if _suffix(p) == ".bib")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(
        target=_worker,
        args=(sender, str(project.root), scope.files, bib_files, code_paths),
        daemon=True,
    )
    worker.start()
    sender.close()
    try:
        if not receiver.poll(TIMEOUT):
            return _failed(
                code_paths,
                f"timed out after {TIMEOUT} s, too many or too large files in scope",
                scope,
                timed_out=True,
            )
        status, payload = receiver.recv()
    except EOFError:
        status, payload = "error", "the references check process ended without a result"
    finally:
        receiver.close()
        if worker.is_alive():
            worker.kill()
        worker.join()
    if status == "error":
        raise JudicataError("internal_error", f"references check failed: {payload}")
    return payload


def _failed(code_paths: bool, problem: str, scope, timed_out: bool = False) -> dict:
    return {
        "passed": False,
        "evidence": {
            "code_paths": code_paths,
            "files_checked": 0,
            "timed_out": timed_out,
            "problem": problem,
            "paths": list(scope.patterns),
        },
    }


def _worker(sender, root: str, files, bib_files, code_paths: bool) -> None:
    try:
        result = ("ok", _Checker(Path(root), bib_files, code_paths).evaluate(files))
    except Exception:  # noqa: BLE001 (reported to the parent as internal_error)
        result = ("error", traceback.format_exc())
    sender.send(result)
    sender.close()


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.casefold()


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ESCAPE = re.compile(r"\\[!-/:-@\[-`{-~]")
_CODE_SPAN = re.compile(
    r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL
)
_LINK = re.compile(
    r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]"  # [text], one level of nested brackets
    r"\(\s*(<[^<>\n]*>|(?:[^\s()]|\([^\s()]*\))*)"  # (destination
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)"  # "title")
)
_DEFINITION = re.compile(
    r"^ {0,3}\[(?!\^)([^\]\n]+)\]:[ \t]*(<[^<>\n]*>|\S+)", re.MULTILINE
)
_HTML_LINK = re.compile(
    r"<(a|img)\b[^>]*?\s(href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE
)
_SCHEME = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
_HTML_ID = re.compile(
    r"<[A-Za-z][^>]*?\s(?:id|name)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE
)
_CITATION_GROUP = re.compile(r"(?<![!\]\\])\[([^\[\]\n]*@[^\[\]\n]*)\](?![(\[:])")
_CITATION_KEY = re.compile(
    r"(?<![\w@.])-?@(?:\{([^{}\n]*)\}|(\w(?:\w|[:.#$%&\-+?<>~/](?=\w))*))"
)
_TEX_CITE = re.compile(
    r"\\[A-Za-z]*cite[A-Za-z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}"
)
_TEX_COMMENT = re.compile(r"(?<!\\)%[^\n]*")
_TEX_VERBATIM = re.compile(
    r"\\begin\{(verbatim|lstlisting|minted|comment)(\*?)\}.*?\\end\{\1\2\}",
    re.DOTALL,
)
_CODE_PATH = re.compile(r"^\S*/\S*[^/.]\.[A-Za-z0-9]+$")
_BIB_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
_ATX = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_HEADING_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_LINK = re.compile(r"\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])")
_HEADING_TAG = re.compile(r"<[^>]*>")
_EMPHASIS_UNDERSCORE = re.compile(r"(?<![^\W_])_+|_+(?![^\W_])")


def bib_keys(text: str) -> set[str]:
    """The entry keys of a BibTeX file; @comment, @string and @preamble
    blocks are skipped as a whole."""
    keys, position = set(), 0
    while (match := _BIB_ENTRY.search(text, position)) is not None:
        kind, opening = match.group(1).lower(), match.group(2)
        if kind in ("comment", "string", "preamble"):
            position = _closing(text, match.end(), opening)
            continue
        end = len(text)
        for stop in (",", "}", ")"):
            found = text.find(stop, match.end())
            if found != -1:
                end = min(end, found)
        key = text[match.end() : end].strip()
        if key:
            keys.add(key)
        position = _closing(text, match.end(), opening)
    return keys


def _closing(text: str, start: int, opening: str) -> int:
    """The offset after the bracket that closes the one before start."""
    closing = "}" if opening == "{" else ")"
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading text (github-slugger): the rendered text
    in lower case, everything but letters, digits, marks, '_', '-' and spaces
    dropped, spaces become '-'."""
    text = _HEADING_IMAGE.sub("", heading)
    text = _HEADING_LINK.sub(r"\1", text)
    text = _HEADING_TAG.sub("", text)
    text = _EMPHASIS_UNDERSCORE.sub("", text)
    kept = (
        char
        for char in text.lower()
        if char in "-_ " or unicodedata.category(char)[0] in "LNM"
    )
    return "".join(kept).replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor of a Markdown text: heading slugs (repeated ones get -1,
    -2, ...) and id or name attributes of HTML tags."""
    masked = _mask_blocks(text)
    lines = masked.split("\n")
    found: set[str] = set()
    counts: dict[str, int] = {}

    def add(title: str) -> None:
        base = result = slug(title.strip())
        while result in counts:
            counts[base] += 1
            result = f"{base}-{counts[base]}"
        counts[result] = 0
        found.add(result)

    paragraph = False
    for number, line in enumerate(lines):
        heading = _ATX.match(line)
        if heading:
            add(heading.group(1) or "")
            paragraph = False
        elif paragraph and _SETEXT.match(line) and not _LIST_ITEM.match(line):
            add(lines[number - 1])
            paragraph = False
        else:
            paragraph = bool(line.strip()) and not _LIST_ITEM.match(line)
    for match in _HTML_ID.finditer(masked):
        found.add(match.group(1) if match.group(1) is not None else match.group(2))
    return found


def _blank(match: re.Match) -> str:
    """The match replaced by spaces, line breaks kept (offsets stay valid)."""
    return re.sub(r"[^\n]", " ", match.group())


def _mask_blocks(text: str) -> str:
    """Fenced and indented code blocks and HTML comments blanked out."""
    lines = text.split("\n")
    fence, in_list, previous_blank, code = None, False, True, False
    for number, line in enumerate(lines):
        if fence is not None:
            stripped = line.strip()
            if stripped.startswith(fence) and set(stripped) == {fence[0]}:
                fence = None
            lines[number] = " " * len(line)
            continue
        opening = _FENCE.match(line)
        if opening:
            fence = opening.group(1)
            lines[number] = " " * len(line)
            previous_blank, code = False, False
            continue
        if not line.strip():
            previous_blank = True
            continue
        expanded = line.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        if indent >= 4 and (code or previous_blank) and not in_list:
            lines[number] = " " * len(line)
            code = True
            continue
        code = False
        if _LIST_ITEM.match(line):
            in_list = True
        elif indent < 2:
            in_list = False
        previous_blank = False
    return _COMMENT.sub(_blank, "\n".join(lines))


class _Checker:
    def __init__(self, root: Path, bib_files: list[str], code_paths: bool):
        self.root = root
        self.bib_files = bib_files
        self.code_paths = code_paths
        self._listings: dict[Path, set[str] | None] = {}
        self._anchors: dict[str, set[str] | None] = {}
        self._keys: set[str] | None = None
        self.broken = pattern._Found()
        self.references = 0

    def evaluate(self, files: list[str]) -> dict:
        skipped, unreadable = pattern._Found(), pattern._Found()
        checked = 0
        for path in files:
            suffix = _suffix(path)
            if suffix not in MARKDOWN and suffix not in LATEX:
                continue
            text, reason = pattern._read(self.root / path)
            if reason == "binary":
                skipped.add({"file": path, "reason": reason})
                continue
            if reason is not None:
                unreadable.add({"file": path, "reason": reason})
                continue
            checked += 1
            self._found = []
            if suffix in MARKDOWN:
                self._markdown(path, text)
            else:
                self._latex(path, text)
            for item in sorted(self._found, key=lambda b: (b["line"], b["column"])):
                self.broken.add(item)
        evidence = {
            "code_paths": self.code_paths,
            "files_checked": checked,
            "references_checked": self.references,
            "timed_out": False,
        }
        evidence |= self.broken.as_evidence("broken", "broken_count")
        evidence |= skipped.as_evidence("skipped", "skipped_count")
        evidence |= unreadable.as_evidence("unreadable", "unreadable_count")
        passed = self.broken.count == 0
        if unreadable.count:
            passed = False
            evidence["problem"] = (
                f"{unreadable.count} file(s) in scope could not be read, "
                "so their references could not be checked"
            )
        elif checked == 0:
            evidence["note"] = (
                "no Markdown or LaTeX files in scope, there are no references to check"
            )
        return {"passed": passed, "evidence": evidence}

    def _markdown(self, path: str, text: str) -> None:
        spans = []

        def code(match: re.Match) -> str:
            spans.append(match)
            return _blank(match)

        masked = _CODE_SPAN.sub(code, _ESCAPE.sub(_blank, _mask_blocks(text)))
        if self.code_paths:
            for span in spans:
                content = text[span.start(2) : span.end(2)]
                self._code_path(path, text, span.start(2), content.strip())
        for match in _LINK.finditer(masked):
            raw, start = match.group(3), match.start(3)
            kind = "image" if match.group(1) else "link"
            self._link(path, text, start, text[start : start + len(raw)], kind)
        for match in _DEFINITION.finditer(masked):
            start = match.start(2)
            self._link(path, text, start, text[start : match.end(2)], "link")
        for match in _HTML_LINK.finditer(masked):
            group = 3 if match.group(3) is not None else 4
            kind = "image" if match.group(1).lower() == "img" else "link"
            start = match.start(group)
            self._link(path, text, start, text[start : match.end(group)], kind)
        for match in _CITATION_GROUP.finditer(masked):
            for key in _CITATION_KEY.finditer(match.group(1)):
                group = 1 if key.group(1) is not None else 2
                start = match.start(1) + key.start(group)
                self._citation(path, text, start, key.group(group).strip())
        self._tex_citations(path, text, masked)

    def _code_path(self, path: str, text: str, offset: int, span: str) -> None:
        """An inline code span that is a single path with a slash and a file
        extension, relative to the project root."""
        if not _CODE_PATH.match(span) or _SCHEME.match(span):
            return
        self.references += 1
        target = os.path.normpath(span.lstrip("/")).replace(os.sep, "/")
        offset += text[offset:].index(span)
        place = (path, text, offset, "code_path", span, target)
        if target == ".." or target.startswith("../"):
            self._broken(*place, "points outside the project")
        elif not self._exists(target):
            self._broken(
                *place, f"{target} does not exist (relative to the project root)"
            )

    def _latex(self, path: str, text: str) -> None:
        masked = _TEX_COMMENT.sub(_blank, _TEX_VERBATIM.sub(_blank, text))
        self._tex_citations(path, text, masked)

    def _tex_citations(self, path: str, text: str, masked: str) -> None:
        for match in _TEX_CITE.finditer(masked):
            offset = match.start(1)
            for part in match.group(1).split(","):
                key = part.strip()
                if key and key != "*":
                    self._citation(path, text, offset + part.index(key), key)
                offset += len(part) + 1

    def _citation(self, path: str, text: str, offset: int, key: str) -> None:
        self.references += 1
        if self._keys is None:
            self._keys = set()
            for bib in self.bib_files:
                content, reason = pattern._read(self.root / bib)
                if reason is None:
                    self._keys |= bib_keys(content)
        if key in self._keys:
            return
        if self.bib_files:
            reason = f"{key} is not a key in any .bib file of the project"
        else:
            reason = f"{key} cannot be found, the project has no .bib file"
        self._broken(path, text, offset, "citation", key, key, reason)

    def _link(self, path: str, text: str, offset: int, raw: str, kind: str) -> None:
        reference = raw.strip()
        destination = reference
        if destination.startswith("<") and destination.endswith(">"):
            destination = destination[1:-1].strip()
        if not destination or _SCHEME.match(destination):
            return
        self.references += 1
        location, has_anchor, anchor = destination.partition("#")
        location = unquote(location.split("?", 1)[0])
        if not location:
            location = PurePosixPath(path).name
        if location.startswith("/"):
            joined = location.lstrip("/")
        else:
            joined = str(PurePosixPath(path).parent / location)
        target = os.path.normpath(joined).replace(os.sep, "/")
        place = (path, text, offset, kind, reference, target)
        if target == ".." or target.startswith("../"):
            self._broken(*place, "points outside the project")
        elif not self._exists(target):
            self._broken(*place, f"{target} does not exist")
        elif has_anchor and anchor and not self._has_anchor(target, unquote(anchor)):
            self._broken(*place, f"no heading or anchor #{anchor} in {target}")

    def _has_anchor(self, target: str, anchor: str) -> bool:
        """Whether a Markdown file has the anchor; anchors into other files
        (line anchors of source files, for example) are not checked."""
        if _suffix(target) not in MARKDOWN:
            return True
        if target not in self._anchors:
            text, reason = pattern._read(self.root / target)
            self._anchors[target] = None if reason else anchors(text)
        known = self._anchors[target]
        if known is None:  # a directory or unreadable: nothing to look up
            return True
        return anchor in known or anchor.lower() in known

    def _exists(self, relative: str) -> bool:
        """Whether the path exists with exactly this spelling (also on file
        systems that ignore case)."""
        if relative in ("", "."):
            return True
        current = self.root
        for part in relative.split("/"):
            listing = self._listing(current)
            if listing is None or part not in listing:
                return False
            current = current / part
        return True

    def _listing(self, directory: Path) -> set[str] | None:
        if directory not in self._listings:
            try:
                self._listings[directory] = set(os.listdir(directory))
            except OSError:
                self._listings[directory] = None
        return self._listings[directory]

    def _broken(self, path, text, offset, kind, reference, target, reason) -> None:
        start = text.rfind("\n", 0, offset) + 1
        self._found.append(
            {
                "file": path,
                "line": text.count("\n", 0, offset) + 1,
                "column": offset - start + 1,
                "kind": kind,
                "reference": reference[:MAX_REFERENCE],
                "target": target,
                "reason": reason,
            }
        )
