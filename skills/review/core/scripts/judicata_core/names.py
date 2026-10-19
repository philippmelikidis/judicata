"""Names of the judicata skills as the user's install shows them.

The plugin (Claude Code, Codex) has `/judicata:review` and the others. Plain
skill installs (`npx skills`) have the prefixed copies `judicata-review`,
`judicata-contract` and `judicata-status`, and their core lives in
`<skills>/judicata-review/core/`. So the core knows which names to print from
where it lies.
"""

from pathlib import Path

PLAIN = Path(__file__).resolve().parents[3].name == "judicata-review"


def skill(name: str) -> str:
    """`/judicata:<name>` in a plugin, `/judicata-<name>` in a plain install."""
    return f"/judicata-{name}" if PLAIN else f"/judicata:{name}"
