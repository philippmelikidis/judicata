#!/usr/bin/env python3
"""Copy the core into the review skill, for installs as plain Agent Skills.

`npx skills add` copies only the skill folders (skills/<name>/), not the plugin
root. So skills/review/core/ carries a copy of everything the skills need from
the plugin root: scripts/judicata.py with the package scripts/judicata_core/,
templates/, agents/reviewer.md, agents/verifier.md and LICENSE, in the same
layout (the core finds its templates relative to itself).

The source of truth stays at the plugin root. The check compares the contents and
the executable bit (Git tracks it). Never edit the copy by hand:

  python3 tools/bundle_core.py          rebuild skills/review/core/
  python3 tools/bundle_core.py --check  exit 1 and list every difference (CI)
"""

import argparse
import shutil
import sys
from pathlib import Path

BUNDLE = Path("skills") / "review" / "core"
SOURCES = [
    Path("scripts") / "judicata.py",
    Path("scripts") / "judicata_core",
    Path("templates"),
    Path("agents") / "reviewer.md",
    Path("agents") / "verifier.md",
    Path("LICENSE"),
]
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc"}


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


def differences(root: Path) -> list[str]:
    source = _files(root, SOURCES)
    bundle_dir = root / BUNDLE
    bundled = {}
    if bundle_dir.is_dir():
        bundled = _files(bundle_dir, [Path(p.name) for p in bundle_dir.iterdir()])
    problems = []
    for path in sorted(source.keys() | bundled.keys()):
        if path not in bundled:
            problems.append(f"missing in {BUNDLE}: {path.as_posix()}")
        elif path not in source:
            problems.append(f"not in the source: {(BUNDLE / path).as_posix()}")
        elif source[path].read_bytes() != bundled[path].read_bytes():
            problems.append(f"differs from the source: {(BUNDLE / path).as_posix()}")
        elif _executable(source[path]) != _executable(bundled[path]):
            problems.append(f"executable bit differs: {(BUNDLE / path).as_posix()}")
    return problems


def build(root: Path) -> None:
    bundle_dir = root / BUNDLE
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    for relative, path in _files(root, SOURCES).items():
        target = bundle_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        shutil.copymode(path, target)  # Git keeps the executable bit (shebang)


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
    missing = [p.as_posix() for p in SOURCES if not (args.root / p).exists()]
    if missing:  # never build or accept a bundle without its parts
        for path in missing:
            print(f"source missing: {path}")
        return 2
    if args.check:
        problems = differences(args.root)
        for problem in problems:
            print(problem)
        if problems:
            print(f"Run: python3 tools/bundle_core.py (rebuilds {BUNDLE.as_posix()}/)")
            return 1
        print(f"{BUNDLE.as_posix()}/ matches the source")
        return 0
    build(args.root)
    print(f"rebuilt {BUNDLE.as_posix()}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
