"""Command line of the core: argument parsing and the JSON envelope.

Success: {"ok": true, ...} on stdout, exit 0.
Failure: {"ok": false, "error": {"code", "message", "details"}} on stdout, exit 1.
Subcommands are grouped (round start|check|close). New groups register a
function in GROUPS; every subcommand takes --project.
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import (
    approvals,
    assignment,
    changes,
    contract,
    decisions,
    findings,
    gate,
    report,
    review,
    rounds,
    session,
    status,
    subagent,
    templates,
    untrusted,
    verifier,
    versions,
)
from .errors import JudicataError
from .project import Project


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JudicataError("usage", message, {"usage": self.format_usage().strip()})


def _project_option() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--project",
        required=True,
        help="project directory; the core writes only below <project>/.judicata/ "
        "and to <project>/.gitattributes",
    )
    return parent


def _state_dir_option() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--state-dir",
        help="directory for this user's local approvals, outside the project "
        "(default: JUDICATA_STATE_DIR, CLAUDE_PLUGIN_DATA, "
        "$XDG_STATE_HOME/judicata, ~/.local/state/judicata)",
    )
    return parent


def _add_round(groups, common) -> None:
    group = groups.add_parser("round", help="run a review round")
    commands = group.add_subparsers(dest="command", required=True)

    start = commands.add_parser(
        "start", parents=[common], help="start a round against a contract"
    )
    start.add_argument("--contract", help="contract name (default: the only one)")
    _scope_options(start, "the round looks only at the contract's files")
    start.add_argument(
        "--criteria",
        type=_names,
        help="focus: comma-separated criterion ids this round judges "
        '("" = none, default: all)',
    )
    start.add_argument(
        "--categories",
        type=_categories,
        help="focus: comma-separated finding categories this round records ("
        + ", ".join(findings.CATEGORIES)
        + '; "" = none, default: all)',
    )
    start.set_defaults(
        handler=lambda a, p: rounds.start(
            p, a.contract, a.paths, a.diff, a.criteria, a.categories
        )
    )

    check = commands.add_parser(
        "check",
        parents=[common, _state_dir_option()],
        help="run the deterministic criteria of a round (commands only if approved)",
    )
    check.add_argument("--round", required=True, help="round id from round start")
    check.set_defaults(handler=lambda a, p: rounds.check(p, a.round, a.state_dir))

    close = commands.add_parser(
        "close", parents=[common], help="close a round and report the outcome"
    )
    close.add_argument("--round", required=True, help="round id from round start")
    close.set_defaults(handler=lambda a, p: rounds.close(p, a.round))

    shown = commands.add_parser(
        "report",
        parents=[common],
        help="the whole result of a round, before any decision question",
    )
    shown.add_argument("--round", required=True, help="round id from round start")
    shown.set_defaults(handler=lambda a, p: report.report(p, a.round))

    finish = commands.add_parser(
        "finish",
        parents=[common],
        help="confirm finishing a required_unmet round (unmet required criteria)",
    )
    finish.add_argument("--round", required=True, help="round id of the closed round")
    finish.add_argument(
        "--confirm",
        required=True,
        metavar="UNMET_HASH",
        help="the unmet_hash round close reported, after the user said yes",
    )
    finish.set_defaults(handler=lambda a, p: rounds.finish(p, a.round, a.confirm))


def _scope_options(command, what: str) -> None:
    command.add_argument(
        "--for",
        dest="paths",
        action="append",
        default=[],
        metavar="PATH",
        help=f"file or directory (repeatable); {what} below these paths",
    )
    command.add_argument(
        "--diff",
        nargs="?",
        const=changes.DEFAULT_BASE,
        metavar="BASE",
        help=f"{what} that changed against BASE (default {changes.DEFAULT_BASE}): "
        "committed, staged, unstaged and untracked, deleted files left out",
    )


def _names(value: str) -> list[str]:
    return list(dict.fromkeys(n.strip() for n in value.split(",") if n.strip()))


def _categories(value: str) -> list[str]:
    names = _names(value)
    unknown = [n for n in names if n not in findings.CATEGORIES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown categories {', '.join(unknown)}, "
            f"choose from {', '.join(findings.CATEGORIES)}"
        )
    return names


def _models(value: str) -> list[str]:
    models = [m.strip() for m in value.split(",")]
    if not all(contract.MODEL_RE.match(m) for m in models):
        raise argparse.ArgumentTypeError("a comma-separated list of model names")
    return models


def _add_review(groups, common) -> None:
    group = groups.add_parser("review", help="reviewers of a round")
    commands = group.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan", parents=[common], help="fix the reviewer runs of a round"
    )
    plan.add_argument("--round", required=True, help="round id from round start")
    plan.add_argument(
        "--count", type=int, help="number of reviewers for this round only"
    )
    plan.add_argument(
        "--models",
        type=_models,
        help="comma-separated models for this round only (inherit, opus, ...)",
    )
    plan.set_defaults(handler=lambda a, p: review.plan(p, a.round, a.count, a.models))

    submit = commands.add_parser(
        "submit",
        parents=[common],
        help="take in the JSON output of one reviewer run (stdin or --file)",
    )
    submit.add_argument("--round", required=True, help="round id from round start")
    submit.add_argument("--run", required=True, help="run id from review plan")
    submit.add_argument("--file", help="read the output from this file, not stdin")
    submit.set_defaults(
        handler=lambda a, p: review.submit(p, a.round, a.run, _read_input(a.file))
    )

    discard = commands.add_parser(
        "discard", parents=[common], help="give up on a run that produced nothing"
    )
    discard.add_argument("--round", required=True, help="round id from round start")
    discard.add_argument("--run", required=True, help="run id from review plan")
    discard.add_argument("--reason", required=True, help="one line, for the report")
    discard.set_defaults(
        handler=lambda a, p: review.discard(p, a.round, a.run, a.reason)
    )

    verdicts = commands.add_parser(
        "verdicts",
        parents=[common],
        help="consensus per judgment criterion and decisions still needed",
    )
    verdicts.add_argument("--round", required=True, help="round id from round start")
    verdicts.set_defaults(handler=lambda a, p: review.verdicts(p, a.round))

    findings = commands.add_parser(
        "findings",
        parents=[common],
        help="consensus over the findings outside the contract",
    )
    findings.add_argument("--round", required=True, help="round id from round start")
    findings.set_defaults(handler=lambda a, p: review.findings_of(p, a.round))

    decide = commands.add_parser(
        "decide",
        parents=[common],
        help="record the user's verdict where the reviewers disagree",
    )
    decide.add_argument("--round", required=True, help="round id from round start")
    decide.add_argument("--criterion", required=True, help="judgment criterion id")
    decide.add_argument(
        "--passed", required=True, choices=["true", "false"], help="the verdict"
    )
    decide.add_argument("--reason", required=True, help="one line, why")
    decide.set_defaults(
        handler=lambda a, p: review.decide(
            p, a.round, a.criterion, a.passed == "true", a.reason
        )
    )


def _add_verify(groups, common) -> None:
    group = groups.add_parser(
        "verify", help="verifiers that try to refute the confirmed blockers of a round"
    )
    commands = group.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan",
        parents=[common],
        help="fix the verifier runs of a round, one blocker each, most severe first",
    )
    plan.add_argument("--round", required=True, help="round id from round start")
    plan.set_defaults(handler=lambda a, p: verifier.plan(p, a.round))

    more = commands.add_parser(
        "more",
        parents=[common],
        help="on the user's request: one run for each blocker over the limit",
    )
    more.add_argument("--round", required=True, help="round id from round start")
    more.set_defaults(handler=lambda a, p: verifier.more(p, a.round))

    submit = commands.add_parser(
        "submit",
        parents=[common],
        help="take in the JSON verdict of one verifier run (stdin or --file)",
    )
    submit.add_argument("--round", required=True, help="round id from round start")
    submit.add_argument("--run", required=True, help="run id from verify plan")
    submit.add_argument("--file", help="read the output from this file, not stdin")
    submit.add_argument(
        "--self",
        dest="self_verified",
        action="store_true",
        help="the session verified the blocker itself (no sub-agent): not independent",
    )
    submit.set_defaults(
        handler=lambda a, p: verifier.submit(
            p, a.round, a.run, _read_input(a.file), not a.self_verified
        )
    )

    discard = commands.add_parser(
        "discard", parents=[common], help="give up on a run that produced nothing"
    )
    discard.add_argument("--round", required=True, help="round id from round start")
    discard.add_argument("--run", required=True, help="run id from verify plan")
    discard.add_argument("--reason", required=True, help="one line, for the report")
    discard.set_defaults(
        handler=lambda a, p: verifier.discard(p, a.round, a.run, a.reason)
    )

    reopen = commands.add_parser(
        "reopen",
        parents=[common],
        help="the user disagrees with a refutation: the finding blocks again",
    )
    reopen.add_argument("--round", required=True, help="round of the verdict")
    reopen.add_argument("--fingerprint", required=True, help="from finding list")
    reopen.add_argument("--reason", required=True, help="one line, why")
    reopen.set_defaults(
        handler=lambda a, p: verifier.reopen(p, a.round, a.fingerprint, a.reason)
    )


def _read_input(path: str | None) -> str:
    if path is None:
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise JudicataError("input_unreadable", f"cannot read {path}: {exc}", {})


def _add_template(groups, common) -> None:
    group = groups.add_parser("template", help="templates for the contract interview")
    commands = group.add_subparsers(dest="command", required=True)

    list_ = commands.add_parser(
        "list", parents=[common], help="list templates, optionally suggest one"
    )
    list_.add_argument(
        "--for",
        dest="paths",
        action="append",
        default=[],
        metavar="PATH",
        help="artifact file or directory (repeatable); suggests a template for it",
    )
    list_.set_defaults(handler=lambda a, p: templates.list_(p, a.paths))

    show = commands.add_parser(
        "show", parents=[common], help="a template and a contract proposal from it"
    )
    show.add_argument("--template", required=True, help="code, docs or prose")
    show.set_defaults(handler=lambda a, p: templates.show(a.template))


def _add_contract(groups, common) -> None:
    group = groups.add_parser(
        "contract", help="create, change, show, approve and assign contracts"
    )
    commands = group.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate",
        parents=[common],
        help="validate a proposed contract (JSON on stdin), write nothing",
    )
    validate.set_defaults(handler=lambda a, p: versions.validate(p, _stdin_json()))

    create = commands.add_parser(
        "create",
        parents=[common, _state_dir_option()],
        help="store a new contract (JSON on stdin) as its first version, "
        "approved for its author",
    )
    create.set_defaults(
        handler=lambda a, p: versions.create(p, _stdin_json(), a.state_dir)
    )

    change = commands.add_parser(
        "change",
        parents=[common, _state_dir_option()],
        help="compare a changed contract (JSON on stdin, or the file with "
        "--from-file) with its latest version; store it with --confirm",
    )
    change.add_argument("--contract", help="contract name (default: the only one)")
    change.add_argument(
        "--from-file",
        action="store_true",
        help="take the contract file as edited by hand instead of stdin",
    )
    change.add_argument(
        "--confirm",
        metavar="CHANGE_HASH",
        help="change_hash of the difference the user confirmed; stores the "
        "next version and approves its commands",
    )
    change.set_defaults(
        handler=lambda a, p: versions.change(
            p,
            None if a.from_file else _stdin_json(),
            a.contract,
            a.from_file,
            a.confirm,
            a.state_dir,
        )
    )

    show = commands.add_parser(
        "show",
        parents=[common, _state_dir_option()],
        help="the current contract, its version and whether its commands are approved",
    )
    show.add_argument("--contract", help="contract name (default: the only one)")
    show.set_defaults(handler=lambda a, p: versions.show(p, a.contract, a.state_dir))

    approve = commands.add_parser(
        "approve",
        parents=[common, _state_dir_option()],
        help="approve the commands of a contract for this user and project",
    )
    approve.add_argument("--contract", help="contract name (default: the only one)")
    approve.add_argument(
        "--commands-hash",
        required=True,
        help="commands_hash of the commands shown to the user (from round check "
        "or contract show)",
    )
    approve.set_defaults(
        handler=lambda a, p: approvals.approve(
            p, a.contract, a.commands_hash, a.state_dir
        )
    )

    resolve = commands.add_parser(
        "resolve",
        parents=[common],
        help="assign the files of an artifact to contracts by their path patterns",
    )
    _scope_options(resolve, "only the files")
    resolve.set_defaults(handler=_resolve)

    assign = commands.add_parser(
        "assign",
        parents=[common],
        help="record which contract a path belongs to when several match",
    )
    assign.add_argument(
        "--for", dest="path", required=True, metavar="PATH", help="file or directory"
    )
    assign.add_argument("--contract", required=True, help="the chosen contract")
    assign.add_argument(
        "--among",
        action="append",
        required=True,
        metavar="NAME",
        help="every contract in question, as resolve listed them (repeatable)",
    )
    assign.set_defaults(
        handler=lambda a, p: assignment.assign(p, a.path, a.contract, a.among)
    )


def _add_finding(groups, common) -> None:
    group = groups.add_parser("finding", help="findings, their states and decisions")
    commands = group.add_subparsers(dest="command", required=True)

    list_ = commands.add_parser(
        "list",
        parents=[common],
        help="findings of a round, or of a contract with the register of accepted "
        "risks",
    )
    which = list_.add_mutually_exclusive_group()
    which.add_argument("--round", help="round id from round start")
    which.add_argument("--contract", help="contract name (default: the only one)")
    list_.set_defaults(
        handler=lambda a, p: (
            decisions.list_round(p, a.round)
            if a.round
            else decisions.list_contract(p, a.contract)
        )
    )

    decide = commands.add_parser(
        "decide",
        parents=[common],
        help="record the user's decision on a finding: fix, reject or accept",
    )
    decide.add_argument("--round", required=True, help="round the finding is in")
    decide.add_argument("--fingerprint", required=True, help="from finding list")
    decide.add_argument(
        "--decision",
        required=True,
        choices=decisions.CHOICES,
        help="keep: hold the previous decision of a finding whose place changed",
    )
    decide.add_argument(
        "--reason",
        help="one line, why (required for reject and accept; keep reuses the old one)",
    )
    decide.add_argument(
        "--reopen-if",
        dest="reopen",
        help="when the finding counts again (required for reject and accept; keep "
        "reuses the old one)",
    )
    decide.set_defaults(
        handler=lambda a, p: decisions.decide(
            p, a.round, a.fingerprint, a.decision, a.reason, a.reopen
        )
    )


def _resolve(args, project: Project) -> dict:
    if not args.paths and args.diff is None:
        raise JudicataError("usage", "name the artifact with --for PATH or --diff", {})
    if args.diff is None:
        return assignment.resolve(project, args.paths)
    found = changes.changed(project, args.diff)
    result = assignment.resolve(project, args.paths or ["."], found.files)
    result["diff"] = {
        "base": found.base,
        "merge_base": found.merge_base,
        "file_count": len(found.files),
        "deleted": found.deleted[: changes.MAX_LISTED],
        "deleted_count": len(found.deleted),
    }
    return result


def _add_scope(groups, common) -> None:
    group = groups.add_parser(
        "scope", help="turn what the user names into files: a diff, a module"
    )
    commands = group.add_subparsers(dest="command", required=True)

    diff = commands.add_parser(
        "diff",
        parents=[common],
        help="files changed against a branch or commit, renames under the new name, "
        "deleted files listed apart",
    )
    diff.add_argument(
        "--base",
        default=changes.DEFAULT_BASE,
        help=f"branch or commit to compare with (default {changes.DEFAULT_BASE})",
    )
    diff.set_defaults(handler=lambda a, p: changes.diff(p, a.base))

    find = commands.add_parser(
        "find", parents=[common], help="files and directories a module name stands for"
    )
    find.add_argument(
        "--name", required=True, help="module, file or directory name (core.ledger)"
    )
    find.set_defaults(handler=lambda a, p: changes.find(p, a.name))


def _add_status(groups, common) -> None:
    command = groups.add_parser(
        "status",
        parents=[common],
        help="per contract: outcome, round in the pass, open blockers and should-fix, "
        "accepted risks and rejections (reads the ledger, writes nothing)",
    )
    command.add_argument("--contract", help="only this contract (default: all)")
    command.set_defaults(handler=lambda a, p: status.show(p, a.contract))


def _add_session(groups, common) -> None:
    group = groups.add_parser(
        "session", help="what a new session needs to know about the ledger"
    )
    commands = group.add_subparsers(dest="command", required=True)
    summary = commands.add_parser(
        "summary",
        parents=[common],
        help="the ledger summary the SessionStart hook gives a new session, for "
        "hosts without hooks (writes nothing)",
    )
    summary.set_defaults(handler=lambda a, p: session.summary(p))


GROUPS = [
    _add_round,
    _add_review,
    _add_verify,
    _add_finding,
    _add_template,
    _add_contract,
    _add_scope,
    _add_status,
    _add_session,
]


def _stdin_json():
    if sys.stdin is None or sys.stdin.isatty():
        raise JudicataError("invalid_input", "pass the input as JSON on stdin")
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JudicataError(
            "invalid_input", f"stdin is not UTF-8 (byte {exc.start})"
        ) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudicataError(
            "invalid_input",
            f"stdin is not valid JSON: {exc.msg} (line {exc.lineno})",
            {"line": exc.lineno, "column": exc.colno},
        ) from None


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="judicata", description="judicata core, JSON in and out")
    groups = parser.add_subparsers(dest="group", required=True)
    common = _project_option()
    for add in GROUPS:
        add(groups, common)
    return parser


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:  # hook output, not the JSON envelope
        if argv[1:2] == ["stop"]:
            return gate.main(argv[1:])  # gate.py
        if argv[1:2] == ["subagent-stop"]:
            return subagent.main(argv[1:])  # subagent.py
        return session.main(argv[1:])  # session.py
    try:
        args = build_parser().parse_args(argv)
        payload = args.handler(args, Project(args.project))
    except JudicataError as exc:
        _emit({"ok": False, "error": _cleaned(exc.to_json())})
        return 1
    except Exception as exc:  # noqa: BLE001 (a bug in the core still answers in JSON)
        error = JudicataError(
            "internal_error",
            untrusted.clean(f"unexpected {type(exc).__name__}: {exc}", 500),
            {"traceback": traceback.format_exc()},
        )
        _emit({"ok": False, "error": error.to_json()})
        return 1
    _emit({"ok": True, **payload})
    return 0


def _cleaned(value):
    """An error for output: its strings can quote the ledger, the contract or a
    path, so line breaks and hidden characters become visible escapes (exact
    otherwise: details are data, for example the commands to approve)."""
    if isinstance(value, str):
        return untrusted.visible(value)
    if isinstance(value, list):
        return [_cleaned(v) for v in value]
    if isinstance(value, dict):
        return {k: _cleaned(v) for k, v in value.items()}
    return value


def _emit(payload: dict) -> None:
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except UnicodeEncodeError:  # a lone surrogate from the ledger: escape it
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
