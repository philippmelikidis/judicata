"""Templates: proposals for the contract interview, files in <plugin>/templates/.

A template is a contract without paths plus interview hints:
  name, description, priority (lower wins a tie), suggest_for (globs of artifacts
  it fits), [[criteria]] as in a contract, each optionally with
  fill = [fields the interview must replace from the project] and hint = "how".
Templates are never used as contracts directly: `template show` returns a
proposal in the input shape of `contract create`, which the interview edits.
"""

import fnmatch
import os
import tomllib
from pathlib import Path

from . import contract
from .errors import JudicataError
from .project import Project

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
TEMPLATE_KEYS = {"name", "description", "priority", "suggest_for", "criteria"}
HINT_KEYS = {"fill", "hint"}
MAX_WALK = 5000  # files looked at per directory given to --for
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "site-packages", "vendor"}


def names() -> list[str]:
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.toml"))


def load(name: str) -> dict:
    available = names()
    if name not in available:
        raise JudicataError(
            "template_not_found",
            f"no template named {name!r}",
            {"template": name, "templates": available},
        )
    path = TEMPLATES_DIR / f"{name}.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _broken(path, f"not valid TOML: {exc}") from None
    unknown = sorted(set(raw) - TEMPLATE_KEYS)
    if unknown or raw.get("name") != name:
        raise _broken(path, f"unknown fields {unknown} or name is not {name!r}")
    if not isinstance(raw.get("criteria"), list) or not all(
        isinstance(item, dict) for item in raw["criteria"]
    ):
        raise _broken(path, "criteria must be a list of tables")
    stripped = [
        {k: v for k, v in item.items() if k not in HINT_KEYS}
        for item in raw["criteria"]
    ]
    proposal = {
        "name": name,
        "template": name,
        "paths": ["**"],
        "reviewers": dict(contract.DEFAULT_REVIEWERS),
        "stop": dict(contract.DEFAULT_STOP),
        "criteria": stripped,
    }
    try:
        parsed = contract.parse(proposal, path)
    except JudicataError as exc:
        raise _broken(path, exc.message) from None
    criteria = []
    for item, criterion in zip(raw["criteria"], parsed.criteria, strict=True):
        fill = item.get("fill", [])
        fields = {"text", *criterion.params}
        if not isinstance(fill, list) or not set(fill) <= fields:
            raise _broken(path, f"{criterion.id}: fill names unknown fields {fill}")
        criteria.append(
            {
                **contract.criterion_json(criterion),
                "fill": fill,
                "hint": item.get("hint", ""),
            }
        )
    return {
        "name": name,
        "description": raw.get("description", ""),
        "priority": raw.get("priority", 100),
        "suggest_for": list(raw.get("suggest_for", [])),
        "criteria": criteria,
    }


def list_(project: Project, paths: list[str]) -> dict:
    loaded = [load(name) for name in names()]
    files = _files(project, paths)
    counts = {t["name"]: 0 for t in loaded}
    by_priority = sorted(loaded, key=lambda t: (t["priority"], t["name"]))
    for file in files:
        for template in by_priority:
            if _fits(file, template["suggest_for"]):
                counts[template["name"]] += 1
                break
    suggested = None
    if files and any(counts.values()):
        best = max(counts.values())
        suggested = next(t["name"] for t in by_priority if counts[t["name"]] == best)
    return {
        "templates": [
            {
                "name": t["name"],
                "description": t["description"],
                "criteria": len(t["criteria"]),
                "matches": counts[t["name"]],
            }
            for t in loaded
        ],
        "suggested": suggested,
    }


def show(name: str) -> dict:
    template = load(name)
    proposal = {
        "name": name,
        "template": name,
        "paths": [],
        "reviewers": dict(contract.DEFAULT_REVIEWERS),
        "stop": dict(contract.DEFAULT_STOP),
        "criteria": [
            {
                "id": c["id"],
                "text": c["text"],
                "check": c["check"],
                "required": c["required"],
                **c["params"],
            }
            for c in template["criteria"]
        ],
    }
    return {"template": template, "proposal": proposal}


def _files(project: Project, paths: list[str]) -> list[str]:
    """Paths relative to the project, posix style; directories are walked."""
    files: list[str] = []
    for given in paths:
        path = Path(given)
        if not path.is_absolute():
            path = project.root / path
        if path.is_dir():
            for root, dirs, names_ in os.walk(path):
                dirs[:] = sorted(
                    d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                )
                for file in sorted(names_):
                    files.append(_relative(project, Path(root) / file))
                    if len(files) >= MAX_WALK:
                        return files
        else:
            files.append(_relative(project, path))
    return files


def _relative(project: Project, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.root).as_posix()
    except ValueError:
        return path.as_posix()


def _fits(file: str, globs: list[str]) -> bool:
    base = file.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(file, glob) or fnmatch.fnmatchcase(base, glob)
        for glob in globs
    )


def _broken(path: Path, problem: str) -> JudicataError:
    return JudicataError(
        "invalid_template",
        f"template {path.name} is broken: {problem}",
        {"file": str(path)},
    )
