"""Check kinds. Each module provides

    validate(params: dict) -> (normalized_params, [(key, problem), ...])

and, if the core evaluates it deterministically,

    run(project, criterion, scope) -> {"passed": bool, "evidence": {...}}

scope is a scope.Scope; scope.files lists the files of the round.

Kinds without run() are left to the reviewers and stay pending until then.
PENDING_REASON says why a criterion of the kind has no result; for command it
means the commands were not approved (see approvals.py).
"""

from . import command, judgment, pattern

KINDS = {"command": command, "pattern": pattern, "judgment": judgment}
