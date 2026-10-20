#!/usr/bin/env python3
"""Build the copies of the core and the skills for installs as plain Agent Skills.

`npx skills add` copies only skill folders, not the plugin root. Two generated
copies follow from the sources, never edit them by hand:

- skills/review/core/ carries everything the skills need from the plugin root:
  scripts/judicata.py with the package scripts/judicata_core/, templates/,
  agents/reviewer.md and LICENSE, in the same layout (the core finds its
  templates relative to itself). The plugin keeps using the root.
- .agents/skills/judicata-<name>/ is a copy of every plugin skill skills/<name>/
  (with its core) under the prefixed name, so that plain installs get
  judicata-review, judicata-contract and judicata-status instead of names that
  collide with host commands. In SKILL.md only the name and the references
  between the skills change (judicata:review, <skills>/review/, "the review
  skill", ...), and the frontmatter drops `metadata.internal`. The plugin skills
  carry `metadata: {internal: true}`, which hides them from npx skills; Claude
  Code and Codex do not read .agents/skills/ for a plugin.

The check compares the contents and the executable bit (Git tracks it):

  python3 tools/bundle_core.py          rebuild both copies
  python3 tools/bundle_core.py --check  exit 1 and list every difference (CI)

Missing sources, a skill folder without SKILL.md, a plugin skill without
`internal: true` directly below `metadata:` or a skills-lock.json at the root
(npx skills would then skip the copies as installed) exit 2 and change nothing.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

SKILLS = Path("skills")
BUNDLE = SKILLS / "review" / "core"
COPIES = Path(".agents") / "skills"
PREFIX = "judicata-"
LOCK = Path("skills-lock.json")
SOURCES = [
    Path("scripts") / "judicata.py",
    Path("scripts") / "judicata_core",
    Path("templates"),
    Path("agents") / "reviewer.md",
    Path("LICENSE"),
]
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc"}
FRONTMATTER = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)


def _files(base: Path, entries: list[Path]) -> dict[Path, Path]:
    """Relative path -> absolute path of every file below the entries."""
    found = {}
    for entry in entries:
        path = base / entry
        if path.is_file():
            found[entry] = path
            continue
        for item in sorted(path.rglob("*")):
            relative = item.relative_to(base)
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if item.is_file() and item.suffix not in SKIP_SUFFIXES:
                found[relative] = item
    return found


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def _skills(root: Path) -> list[str]:
    return sorted(p.parent.name for p in (root / SKILLS).glob("*/SKILL.md"))


def _frontmatter(text: str) -> str:
    match = FRONTMATTER.match(text)
    return match.group(1) if match else ""


def _internal(frontmatter: str) -> tuple[bool, str]:
    """(has `internal: true` directly below `metadata:`, frontmatter without it).

    npx skills needs the boolean true; a deeper `internal` does not count.
    """
    lines = frontmatter.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if line != "metadata:\n":
            continue
        end = start + 1
        while end < len(lines) and lines[end][:1] in (" ", "\t"):
            end += 1
        children = lines[start + 1 : end]
        indent = re.match(r"[ \t]*", children[0]).group() if children else ""
        kept = [child for child in children if child != f"{indent}internal: true\n"]
        if len(kept) == len(children):
            return False, frontmatter
        block = [line, *kept] if kept else []  # drop an empty metadata
        return True, "".join(lines[:start] + block + lines[end:])
    return False, frontmatter


def prefixed(text: str, skill: str, names: list[str]) -> str:
    """SKILL.md of the plugin skill `skill` as its prefixed copy."""
    frontmatter = _frontmatter(text)
    if frontmatter:
        body = text[len(frontmatter) + 8 :]  # both "---\n" lines
        copy = _internal(frontmatter)[1]
        # the folder name, as the Agent Skills spec requires
        copy = re.sub(
            r"^name:.*$", f"name: {PREFIX}{skill}", copy, count=1, flags=re.MULTILINE
        )
        text = f"---\n{copy}---\n{body}"
    name = "|".join(re.escape(n) for n in names)
    alone = rf"(?<![\w-])(?:{name})(?![\w-])"  # never judicata-judicata-review
    # /judicata:review and judicata:review, never judicata:reviewer (the agent)
    text = re.sub(rf"\bjudicata:({name})(?![\w-])", rf"{PREFIX}\1", text)
    # <skills>/review/, ${CLAUDE_PLUGIN_ROOT}/skills/contract/SKILL.md
    text = re.sub(rf"(<skills>/|(?<![\w-])skills/)({name})/", rf"\1{PREFIX}\2/", text)
    # "the review skill", "the `review` skill", also across a line break
    text = re.sub(rf"(`?)({alone})(`?\s+skills?\b)", rf"\1{PREFIX}\2\3", text)
    # "(review, contract, status)"
    return re.sub(
        rf"\(({alone}(?:, {alone})*)\)",
        lambda m: "(" + ", ".join(PREFIX + n for n in m.group(1).split(", ")) + ")",
        text,
    )


def expected(root: Path) -> dict[Path, tuple[bytes, bool]]:
    """Every generated file (relative to the root) -> (content, executable)."""
    core = {
        BUNDLE / relative: (path.read_bytes(), _executable(path))
        for relative, path in _files(root, SOURCES).items()
    }
    names = _skills(root)
    result = dict(core)
    for name in names:
        source = SKILLS / name
        files = {
            source / relative: (path.read_bytes(), _executable(path))
            for relative, path in _files(root / source, [Path(".")]).items()
            if not (source / relative).is_relative_to(BUNDLE)
        }
        # the core of the copy comes from the source, never from a stale bundle
        files.update({p: v for p, v in core.items() if p.is_relative_to(source)})
        for path, (content, executable) in files.items():
            if path == source / "SKILL.md":
                text = prefixed(content.decode("utf-8"), name, names)
                content = text.encode("utf-8")
            target = COPIES / f"{PREFIX}{name}" / path.relative_to(source)
            result[target] = (content, executable)
    return result


def _generated_dirs(root: Path) -> list[Path]:
    """The folders the tool owns: the bundle and every judicata-* copy."""
    dirs = [BUNDLE]
    if (root / COPIES).is_dir():
        dirs += sorted(
            p.relative_to(root)
            for p in (root / COPIES).iterdir()
            if p.is_dir() and p.name.startswith(PREFIX)
        )
    return dirs


def source_problems(root: Path) -> list[str]:
    problems = [
        f"source missing: {p.as_posix()}" for p in SOURCES if not (root / p).exists()
    ]
    if (root / SKILLS).is_dir():
        for folder in sorted((root / SKILLS).iterdir()):
            if folder.is_dir() and not (folder / "SKILL.md").is_file():
                problems.append(
                    f"{SKILLS.as_posix()}/{folder.name}/SKILL.md is missing "
                    "(the build would drop the copy of this skill)"
                )
    for name in _skills(root):
        path = SKILLS / name / "SKILL.md"
        text = (root / path).read_text(encoding="utf-8")
        if not _internal(_frontmatter(text))[0]:
            problems.append(
                f"{path.as_posix()}: the frontmatter needs `metadata:` with "
                "`internal: true` (hides the plugin skill from npx skills)"
            )
    if (root / LOCK).exists():
        problems.append(
            f"{LOCK.as_posix()} at the repository root: npx skills would skip "
            f"the copies in {COPIES.as_posix()}/ as installed, delete it"
        )
    return problems


def differences(root: Path) -> list[str]:
    wanted = expected(root)
    present = {}
    for directory in _generated_dirs(root):
        if (root / directory).is_dir():
            present.update(_files(root, [directory]))
    problems = []
    for path in sorted(wanted.keys() | present.keys()):
        shown = path.as_posix()
        if path not in present:
            problems.append(f"missing: {shown}")
        elif path not in wanted:
            problems.append(f"not in the source: {shown}")
        elif wanted[path][0] != present[path].read_bytes():
            problems.append(f"differs from the source: {shown}")
        elif wanted[path][1] != _executable(present[path]):
            problems.append(f"executable bit differs: {shown}")
    return problems


def build(root: Path) -> None:
    wanted = expected(root)  # read everything before deleting a copy
    for directory in _generated_dirs(root):
        if (root / directory).exists():
            shutil.rmtree(root / directory)
    for relative, (content, executable) in wanted.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if executable else 0o644)  # Git keeps the executable bit


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare, change nothing")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root (default: this repository)",
    )
    args = parser.parse_args(argv)
    targets = f"{BUNDLE.as_posix()}/ and {COPIES.as_posix()}/{PREFIX}*/"
    problems = source_problems(args.root)
    if problems:  # never build or accept a copy without its parts
        for problem in problems:
            print(problem)
        return 2
    if args.check:
        problems = differences(args.root)
        for problem in problems:
            print(problem)
        if problems:
            print(f"Run: python3 tools/bundle_core.py (rebuilds {targets})")
            return 1
        print(f"{targets} match the source")
        return 0
    build(args.root)
    print(f"rebuilt {targets}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
