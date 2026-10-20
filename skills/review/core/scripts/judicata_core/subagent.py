"""SubagentStop hook: judicata's own sub-agents answer with a valid JSON object.

hook subagent-stop (run by hooks/hooks.json) reads the hook input (JSON on
stdin). It acts only for sub-agents of judicata, recognised by `agent_type`
(in Claude Code a plugin agent is `<plugin>:<name>`, so `judicata:reviewer`);
every other sub-agent gets no output. For judicata's agent it checks
`last_assistant_message` the way `review submit` checks it first
(review.parse_output: one JSON object, optionally fenced, valid against the
agent's output schema). Invalid and not yet sent back (`stop_hook_active` not
true): it prints

  {"decision": "block", "reason": "<the exact problems>"}

and the host sends the sub-agent back once. A second invalid answer goes
through; the skill hands it to the core, which rejects it as without the hook.
Valid answers, input without a message and every error: no output, exit 0.

The hook checks only the form. Whether criteria, places and quotes fit the
round stays with `review submit`, which validates every answer itself in every
host (the hook does not know the round and writes nothing).

Codex: every sub-agent has `agent_type` "default" (spike in issue #6), so the
hook cannot tell judicata's reviewer from any other sub-agent there and leaves
all of them alone; Codex relies on the core check.
"""

import json
import sys

from . import review, untrusted

MAX_INPUT_BYTES = 8 * 1024 * 1024  # hook input: the answer plus the host's fields
MAX_LISTED = 200  # problems named in the reason, the rest is counted
INTRO = (
    "judicata cannot use your answer: it does not match the output schema. "
    "Fix exactly these problems and answer again with the complete JSON "
    "object and nothing else (no prose, no Markdown):"
)

# agent_type -> the output schema of that agent. The verifier joins here with
# its own schema ("judicata:verifier"), nothing else changes.
AGENTS = {
    "judicata:reviewer": review.output_schema,
}


def main(argv: list[str]) -> int:
    """Entry point of `judicata.py hook subagent-stop`; never raises, always 0."""
    try:
        hook_input = _read_input()
        schema = AGENTS.get(hook_input.get("agent_type"))
        message = hook_input.get("last_assistant_message")
        if schema is None or not isinstance(message, str):
            return 0
        if hook_input.get("stop_hook_active") is True:  # sent back once already
            return 0
        _, problems = review.parse_output(message, schema())
        if not problems:
            return 0
        payload = {"decision": "block", "reason": reason(problems)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BaseException:  # noqa: BLE001 (a hook never disturbs the session)
        return 0
    return 0


def reason(problems: list[dict]) -> str:
    """What the sub-agent reads: every problem as `- field: problem`."""
    lines = [INTRO]
    for item in problems[:MAX_LISTED]:
        # field names and JSON errors quote the answer: one defused line each
        field = untrusted.clean(item["field"], untrusted.LABEL_LIMIT)
        lines.append(f"- {field}: {untrusted.clean(item['problem'])}")
    if len(problems) > MAX_LISTED:
        lines.append(f"- and {len(problems) - MAX_LISTED} more problems")
    return "\n".join(lines)


def _read_input() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        value = json.loads(sys.stdin.buffer.read(MAX_INPUT_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
