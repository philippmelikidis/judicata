"""Contracts: .judicata/contracts/<name>.toml, validated before any use.

Validation collects every problem at once and raises invalid_contract with a
list of {"field", "problem"} entries. Check-specific parameters are validated by
the check module (see checks/). Hashes cover the normalized form (defaults
filled in), so formatting or comments in the TOML do not change them.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import checks, scope, untrusted
from .errors import JudicataError
from .project import Project

TEMPLATES = ("code", "docs", "prose")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
# a model name: inherit, opus, claude-opus-4-1, sonnet[1m], vendor/model:tag
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]{0,99}\Z")

TOP_KEYS = {"name", "template", "paths", "reviewers", "stop", "verifier", "criteria"}
CRITERION_KEYS = {"id", "text", "check", "required"}
DEFAULT_REVIEWERS = {"count": 2, "models": ["inherit"]}
GATES = ("warn", "block")
DEFAULT_STOP = {"max_rounds": 5, "stall_rounds": 2, "gate": "warn"}
DEFAULT_VERIFIER = {"model": "inherit", "max_runs": 5}
# Fields added in 0.2 count in the contract hash only when they differ from
# their default, so a contract written by 0.1 keeps its hash (and its recorded
# version) unchanged.
ADDED_IN_0_2 = {"stop": {"gate": "warn"}, "verifier": DEFAULT_VERIFIER}


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str
    check: str
    required: bool
    params: dict
    hash: str


@dataclass(frozen=True)
class Contract:
    name: str
    template: str
    paths: list[str]
    reviewers: dict
    stop: dict
    verifier: dict
    criteria: list[Criterion]
    hash: str
    file: str = field(compare=False)

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == criterion_id), None)


def criterion_json(criterion: Criterion) -> dict:
    return {
        "id": criterion.id,
        "text": criterion.text,
        "check": criterion.check,
        "required": criterion.required,
        "params": dict(criterion.params),
        "hash": criterion.hash,
    }


def to_json(current: Contract) -> dict:
    return {
        "name": current.name,
        "template": current.template,
        "paths": list(current.paths),
        "reviewers": dict(current.reviewers),
        "stop": dict(current.stop),
        "verifier": dict(current.verifier),
        "criteria": [criterion_json(c) for c in current.criteria],
        "hash": current.hash,
    }


def names(project: Project) -> list[str]:
    if not project.contracts_dir.is_dir():
        return []
    return sorted(p.stem for p in project.contracts_dir.glob("*.toml"))


def select(project: Project, name: str | None) -> Contract:
    """The named contract, or the only one if no name is given."""
    available = names(project)
    if name is None:
        if not available:
            raise JudicataError(
                "no_contract",
                f"no contract found in {project.contracts_dir}",
                {"contracts_dir": str(project.contracts_dir)},
            )
        if len(available) > 1:
            raise JudicataError(
                "contract_ambiguous",
                "several contracts exist, pass --contract with one of them",
                {"contracts": available},
            )
        name = available[0]
    if name not in available:
        raise JudicataError(
            "contract_not_found",
            f"no contract named {name!r}",
            {"contract": name, "contracts": available},
        )
    return load(project.contracts_dir / f"{name}.toml")


def load(path: Path) -> Contract:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise _invalid(path, [{"field": "", "problem": f"not valid TOML: {exc}"}])
    except UnicodeDecodeError:
        raise _invalid(path, [{"field": "", "problem": "file is not UTF-8"}])
    return parse(raw, path)


def parse(raw: dict, path: Path) -> Contract:
    problems: list[dict] = []

    def problem(where: str, text: str) -> None:
        problems.append({"field": where, "problem": text})

    for key in sorted(set(raw) - TOP_KEYS):
        problem(key, "unknown field")

    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        problem("name", "required, lowercase letters, digits, '-' or '_'")
    elif name != path.stem:
        problem("name", f"must match the file name {path.stem!r}")

    template = raw.get("template")
    if template not in TEMPLATES:
        problem("template", f"required, one of {', '.join(TEMPLATES)}")

    paths = raw.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(p, str) and p.strip() for p in paths)
    ):
        problem("paths", "required, a non-empty list of path patterns")
    else:
        for index, pattern in enumerate(paths):
            if any(c in untrusted.LINE_BREAKS or untrusted.hidden(c) for c in pattern):
                problem(
                    f"paths[{index}]",
                    "contains line breaks, control or invisible characters",
                )
            elif (text := scope.check_pattern(pattern)) is not None:
                problem(f"paths[{index}]", text)

    reviewers = _section(raw, "reviewers", DEFAULT_REVIEWERS, problem)
    count = reviewers.get("count")
    if not _is_int(count) or count < 1:
        problem("reviewers.count", "must be a whole number of at least 1")
    models = reviewers.get("models")
    if (
        not isinstance(models, list)
        or not models
        or not all(isinstance(m, str) and MODEL_RE.match(m) for m in models)
    ):
        problem(
            "reviewers.models",
            "must be a non-empty list of model names (letters, digits and . _ - : / [ ])",
        )
    elif _is_int(count) and len(models) not in (1, count):
        problem("reviewers.models", "needs one model for all or one per reviewer")

    stop = _section(raw, "stop", DEFAULT_STOP, problem)
    for key in ("max_rounds", "stall_rounds"):
        if not _is_int(stop.get(key)) or stop[key] < 1:
            problem(f"stop.{key}", "must be a whole number of at least 1")
    if stop.get("gate") not in GATES:
        problem("stop.gate", f"must be one of {', '.join(GATES)}")

    verifier = _section(raw, "verifier", DEFAULT_VERIFIER, problem)
    if not isinstance(verifier.get("model"), str) or not MODEL_RE.match(
        verifier["model"]
    ):
        problem(
            "verifier.model",
            "must be a model name (letters, digits and . _ - : / [ ])",
        )
    if not _is_int(verifier.get("max_runs")) or verifier["max_runs"] < 1:
        problem("verifier.max_runs", "must be a whole number of at least 1")

    criteria: list[Criterion] = []
    raw_criteria = raw.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        problem("criteria", "required, at least one [[criteria]] entry")
        raw_criteria = []
    seen: set[str] = set()
    for index, item in enumerate(raw_criteria):
        where = f"criteria[{index}]"
        problems_before = len(problems)
        if not isinstance(item, dict):
            problem(where, "must be a table")
            continue
        criterion_id = item.get("id")
        if isinstance(criterion_id, str) and ID_RE.match(criterion_id):
            where = f"criteria[{index}] ({criterion_id})"
            if criterion_id in seen:
                problem(f"{where}.id", f"duplicate id {criterion_id!r}")
            seen.add(criterion_id)
        else:
            problem(f"{where}.id", "required, letters, digits, '.', '_' or '-'")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            problem(f"{where}.text", "required, a non-empty string")
        required = item.get("required")
        if not isinstance(required, bool):
            problem(f"{where}.required", "required, true or false")
        kind = item.get("check")
        module = checks.KINDS.get(kind) if isinstance(kind, str) else None
        if module is None:
            known = ", ".join(checks.KINDS)
            problem(
                f"{where}.check",
                f"unknown check {kind!r}, use one of {known}"
                if kind is not None
                else f"required, one of {known}",
            )
            continue
        given = {k: v for k, v in item.items() if k not in CRITERION_KEYS}
        params, param_problems = module.validate(given)
        for key, text_ in param_problems:
            problem(f"{where}.{key}", text_)
        if len(problems) > problems_before:
            continue
        normalized = {
            "id": criterion_id,
            "text": text,
            "check": kind,
            "required": required,
            "params": params,
        }
        criteria.append(Criterion(**normalized, hash=_hash(normalized)))

    if problems:
        raise _invalid(path, problems)

    body = {
        "name": name,
        "template": template,
        "paths": paths,
        "reviewers": {"count": count, "models": models},
        "stop": {k: stop[k] for k in DEFAULT_STOP},
        "verifier": {k: verifier[k] for k in DEFAULT_VERIFIER},
        "criteria": [c.hash for c in criteria],
    }
    return Contract(
        name=name,
        template=template,
        paths=list(paths),
        reviewers=body["reviewers"],
        stop=body["stop"],
        verifier=body["verifier"],
        criteria=criteria,
        hash=_hash(_without_new_defaults(body)),
        file=str(path),
    )


def _without_new_defaults(body: dict) -> dict:
    """The hashed form: fields added in 0.2 left out while at their default."""
    hashed = dict(body)
    for section, defaults in ADDED_IN_0_2.items():
        kept = {
            k: v
            for k, v in body[section].items()
            if k not in defaults or v != defaults[k]
        }
        if kept:
            hashed[section] = kept
        else:
            del hashed[section]
    return hashed


def _section(raw: dict, key: str, defaults: dict, problem) -> dict:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        problem(key, "must be a table")
        return dict(defaults)
    for unknown in sorted(set(value) - set(defaults)):
        problem(f"{key}.{unknown}", "unknown field")
    return {**defaults, **value}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hash(value) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _invalid(path: Path, problems: list[dict]) -> JudicataError:
    summary = "; ".join(
        f"{p['field']}: {p['problem']}" if p["field"] else p["problem"]
        for p in problems
    )
    return JudicataError(
        "invalid_contract",
        f"contract {path.name} is invalid: {summary}",
        {"file": str(path), "problems": problems},
    )
