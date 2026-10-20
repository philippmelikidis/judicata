# judicata reference

The [README](../README.md) gets you started. This page has the details: the contract format,
the three checks, findings and decisions, the stop rule, Codex, other agents, security and
development.

## How a review works

Run `/judicata:review`. Without a contract, judicata first runs a short interview: it suggests
a template that fits the artifact (code, docs or prose), proposes criteria from the template
and from the artifact (for example the test command it finds in your project), and you drop,
add or mark criteria required or optional. Reviewers (2, the model of your chat), the stop
rule (at most 5 rounds, "not converged" after 2 rounds without progress), the stop gate (warn)
and the verifier (the model of your chat, at most 5 runs per round) are one Enter each.
The contract is stored in `.judicata/contracts/<name>.toml`, for example:

```toml
name = "code"
template = "code"
paths = ["src/**"]

[reviewers]
count = 2
models = ["inherit"]

[stop]
max_rounds = 5
stall_rounds = 2
gate = "warn"

[verifier]
model = "inherit"
max_runs = 5

[[criteria]]
id = "CODE-1"
text = "Tests pass"
check = "command"
required = true
command = "pytest -q"
timeout = 600

[[criteria]]
id = "CODE-2"
text = "No print debugging left"
check = "pattern"
required = false
forbid = '^\s*print\('
```

From then on the contract is frozen. judicata runs the criteria, records the round in the
ledger and reports "done" or the blockers with their evidence (for a command: the last lines of
its output, for a pattern: the hits with file and line, for a judgment: the reviewers' reasons
with file, line and quote). `/judicata:contract` shows the current contract and changes it
when you ask: it shows the difference to the current version and stores a new version only
after you confirm. Decisions on unchanged criteria stay valid, those on changed or removed
criteria lapse. No agent changes the contract on its own, and a contract file edited by hand
does not review until you record the edit the same way. judicata never
changes your files apart from `.judicata/` and one line in `.gitattributes`
(`.judicata/ledger.jsonl merge=union`). Commit both.
Reviews on two branches merge without conflicts. Git is optional: every round records the
revision it looked at, the commit plus a hash of the files, and without Git just the hash.

A pattern criterion has exactly one rule, applied to each file matching `paths`: `forbid` or
`require` a regular expression (Python syntax, `^` and `$` match at every line), or allow at
most `max_words` or `max_lines`. Paths are globs relative to the project root (`*`, `**`, a
directory stands for everything below it); files ignored by Git, `.git/` and `.judicata/`
are never in scope.

A judgment criterion (`check = "judgment"`, only a text) is answered by two independent
reviewers with a fresh context and read-only tools, by default on the model of your chat. If
they agree, the result stands. If they disagree, you decide once, and your decision holds in
later rounds as long as the criterion and the files it was about are unchanged. Set the number
and models in the contract (`[reviewers]`) or just say so for one review ("only one reviewer",
"use Opus and Sonnet"); that does not change the contract. Every place a reviewer cites is
checked: the quote must read exactly at the cited file and line (only whitespace and line
breaks may differ). An answer with an invented or misplaced quote goes back to the reviewer
once with the reason, a second invalid answer drops that reviewer for the round.

Contract format 0.2 adds three optional fields; contracts written by judicata 0.1 keep working
unchanged and get the defaults. `[stop] gate` is `"warn"` (default) or `"block"`: whether the
stop gate only warns when an agent ends its turn while the work is not reviewed and done, or
blocks it. `[verifier]` sets the `model` (default `"inherit"`) and `max_runs` (default 5, at
least 1) of the verifier that tries to refute blockers. `check = "references"` checks that
links, anchors, images and citation keys exist, with `code_paths = true` (default false) also
paths written in backticks. The stop gate, the verifier and the references check are still in
development: until they ship, these fields are validated, stored and shown but have no effect,
and a references criterion stays pending with that reason.

The reviewers also report findings outside the contract. Each one is tied to an address that
survives moved lines: the class and function around the line in Python, the heading path and
paragraph number in Markdown, the symbol name in other languages, else the file. Only a
correctness, security or factual finding with a concrete scenario can be a blocker; everything
else is at most a note. A finding counts when both reviewers report it at the same address in
the same category. A blocker only one of them sees is shown as unconfirmed.

After each review you decide the findings: fix, reject or accept the risk (the last two with a
one-line reason and a condition under which the finding counts again). These decisions hold in
every later session. A rejected or accepted finding that a reviewer reports again at the same,
unchanged place is only counted, not shown. If its place changed, judicata shows it again with
your old decision, and "keep decision" is one answer. Rejecting or accepting the findings of a
failed optional criterion, or of findings outside the contract, gives "done with accepted
risks". For a failed required criterion it gives "required criteria not met": judicata names
the criteria with your decisions and asks once more, with "No, keep it open" first. Your yes
holds only for exactly these criteria, places and decisions; if any of them changes, it asks
again. Reviewers get the list of your decisions
in their task, and a new session starts with a short summary of the ledger (contracts, open
findings with blockers first, accepted risks with their reasons, the last outcome per
contract), so it judges like the last one.

You do not need options to say what to review. Say it ("check the software for bugs", "look
at the ledger module", "review my changes against main", "is the cover letter done?") and
judicata works out the scope (files, a module, the changes against a branch including renames,
without deleted files) and the focus (all criteria, or a part such as correctness for "bugs"),
says in one line what it reviews and asks only when your words leave two real readings. In a
project with `.judicata/`, "review this again" starts judicata without the command. A focused
review reports a result for its focus only, never "done" for the whole contract.

A contract can come with a repository you cloned, and its commands would run on your machine.
judicata therefore runs command criteria only after you approved them: contracts you create
yourself are approved at once, for any other contract `/judicata:review` shows the commands
and asks first. The approval is stored on your machine (in the plugin's data directory), not in
the repository, so nothing checked in can approve it. It holds for this project and exactly
these commands; when a command changes, judicata asks again.

After every round judicata applies the stop rule of the contract and says where you stand:
"done" (every required criterion passed and no confirmed blocker is open), "continue", "not
converged" (for `stall_rounds` rounds in a row the number of open blockers did not go below the
round before; the first round is only the starting point, so with the default of 2 this can
happen after round 3 at the earliest) or "round limit reached" (`max_rounds` rounds). Rounds count per contract and scope; after one of
the final results, or after a change of the contract, the next review starts again at
round 1. If "done" holds only because
you rejected findings of optional criteria or outside the contract, or accepted their risk,
judicata says "done with accepted risks" and lists them. A failed required criterion you
decided away counts as an open blocker until you confirm finishing: the result is "required
criteria not met", after your yes "finished despite unmet required criteria" (a final result);
if you keep it open and nothing changes, the pass ends as "not converged". A focused review does not count as a round. The stop rule is a report: judicata
never refuses a review because of it. `/judicata:status` shows every contract at a glance: the
result, the open blockers and should-fix items, the accepted risks and rejected findings with
their reasons, the round within the current pass and the open blockers per round.

## Codex

judicata runs in the Codex CLI too (tested with 0.163), from the same repository:

```
codex plugin marketplace add philippmelikidis/judicata
codex plugin add judicata@judicata
```

Then trust its hook once: start `codex`, open `/hooks` and trust the SessionStart hook of
judicata. Codex runs the hooks of a plugin only after you trusted them and skips them
silently otherwise. Without that step a new session does not get the summary of the ledger,
so it does not know your decisions until a judicata skill runs; ask "where do we stand?"
(the status skill) and it tells you when the summary is missing. `codex exec
--dangerously-bypass-hook-trust` runs the hooks for that one call without trusting them.

Ask in plain words as in Claude Code ("review this again", "is it done?") or name a skill
(`judicata:review`, `judicata:contract`, `judicata:status`). What differs in Codex:

- **Reviewers** are Codex sub-agents, one per reviewer, each started without the
  conversation (a fresh context), on the model of your session. Model names of Claude
  (opus, sonnet) do not exist there; those reviewers use your session's model, and the
  report says so. Codex cannot limit a sub-agent to read-only tools: the reviewers read the
  files with shell commands in your session's sandbox and are told to change nothing, and
  they see the project's `AGENTS.md`. If sub-agents are switched off, the review runs with
  one reviewer, the session itself, and the report says that it was not independent.
- **Questions** come as a numbered list in the chat; answer with the number or in words.
- **Approvals** of a contract's commands are stored in `~/.local/state/judicata` (or
  `$XDG_STATE_HOME/judicata`), not in Claude Code's plugin data directory, so you approve
  once per host. That directory is outside the project: the Codex sandbox may ask you to
  allow writing there when you approve.

## Other agents (plain skills)

Agents that read [Agent Skills](https://agentskills.io) but have no judicata plugin
(OpenCode, Cursor, Gemini CLI, Copilot and others) get the three skills as plain skill
folders through [skills.sh](https://skills.sh):

```bash
npx skills@latest add philippmelikidis/judicata --skill '*' -a <agent>
```

`-a` names the agent (for example `opencode` or `cursor`; leave it out to choose), `-g`
installs for all your projects instead of the current one. The installer copies
`skills/review`, `skills/contract` and `skills/status` from this repository (OpenCode, for
example, reads them from `.agents/skills/`). The review skill carries a copy of the core in
`core/` (the command line, the templates and the reviewer instructions), and the other two
skills use it, so install all three. Update with `npx skills@latest update`. In Claude Code
and Codex use the plugin instead: the plugin gets everything below, and a second install
would give you every skill twice.

Ask in plain words as in Claude Code, or call the skills the way your agent does (their
names are plain `review`, `contract` and `status`, without the `judicata:` prefix; another
skill or command with the same name in your agent can shadow them). What a plain install
does not have:

- **No session hook.** Plain skills cannot ship hooks, so a new session does not start with
  the summary of the ledger, and a chat that is not running a judicata skill does not know
  your decisions. The review and status skills read the summary themselves before anything
  else (`session summary`, the same text the hook gives), and their report says that
  automatic session memory needs the plugin. A free review outside the skills ("look at this
  file") can therefore bring back a finding you rejected; ask for a judicata review instead.
- **No reviewer agent.** The review skill starts the reviewers with your agent's own
  sub-agent tool, each with a fresh context, and asks them to change nothing: read-only by
  instruction only, as in Codex. Model names of Claude (opus, sonnet) become your session's
  model, and the report says so. If your agent has no sub-agents, the review runs with one
  reviewer, the session itself, and the report says that it was not independent.
- **No question buttons.** Without a question tool, questions come as a numbered list in the
  chat, as in Codex.
- **Approvals** of a contract's commands are stored in `~/.local/state/judicata` (or
  `$XDG_STATE_HOME/judicata`), outside the project, once per machine.

Checked with skills 1.7.2: the install layout for OpenCode, the core run from the installed
copy, and Codex 0.163 using the plain skills (without the plugin) to report the status.
OpenCode and Cursor themselves were not run.

## Security

A repository you clone can bring its own `.judicata/`. judicata reads three kinds of text
from it and hands parts of them to the model: the contracts (criterion texts, paths,
commands), the ledger (labels, summaries, reasons, reopen conditions, evidence) and the
artifact itself (the files the reviewers read, quotes, command output). Anyone who can write
to the repository can write any of it, including text meant to steer the model, a prompt
injection.

- **Commands** of a contract run only after you approved them on your machine (see above).
  The approval is stored outside the repository, tied to this project and exactly these
  commands, so nothing checked in can grant it.
- **Text is data.** The session hook, every skill and the reviewer agent carry the same
  rule: text from the ledger, the contract and the artifact is data, never an instruction;
  if any of it reads like one, Claude shows it to you and asks before doing anything it
  asks for.
- **Detection.** The core looks at every ledger and contract text it passes on for typical
  injection patterns: instructions to the model or agent, "ignore previous instructions",
  role switches ("system:", "you are now"), requests for tool calls, shell commands, changes
  to settings, permissions or approvals, keeping things from the user, sending secrets,
  invisible and bidi control characters, code fences, tags like `<system>`, encoded blobs,
  lookalike letters from other scripts and very long texts. Its answers (session context,
  review plan, finding list, contract show) mark each hit per entry ("possible prompt
  injection detected in entry ..."), with the field and the reason, and Claude names them
  when it reports.
- **Cleaning.** Free text in the session context and in the reviewers' task is cut to about
  200 characters (with a note), put on one line, stripped of control, format and bidi
  characters, and code fences and tags are neutralized. It sits in one delimited data block
  that nothing inside can close.

What this does not do: it never blocks text, and detection is a heuristic. A paraphrased
instruction, a language other than English or German, or a request spread over several
fields can pass unmarked, and plain text can be marked. Reviewers read the artifact files in
full, as they must; judicata cannot clean those. Review changes to `.judicata/` in a pull
request like code, and be careful with judicata in a repository you do not trust.

## Development

```
uvx ruff@0.17.1 check . && uvx ruff@0.17.1 format --check .
uv run --with pytest==9.1.1 pytest
```

The tests drive the core command line. `skills/review/core/` is a copy of `scripts/`,
`templates/`, `agents/reviewer.md` and `LICENSE` for installs as plain skills (an install
copies only the skill folders). Never edit it by hand: after changing the source run
`python3 tools/bundle_core.py`; the tests and CI fail while the copy differs
(`python3 tools/bundle_core.py --check` lists the differences). The behavior of the plugin in Claude Code (skills,
reviewer agent, hook) is checked by five acceptance evals in `evals/`, run with
`claude plugin eval` against the plugin and, as a baseline, against Claude without it:

| Case | Checks |
| --- | --- |
| `01-rejected-finding` | a fresh chat does not bring back a finding the user rejected |
| `02-clean-done` | a clean artifact (a cover letter) is done |
| `03-required-blocker` | a failed required criterion is a blocker, nothing is fixed or decided |
| `04-not-converged` | a third round without progress ends as "not converged" |
| `05-module-scope` | "Prüf das Modul ledger" reviews exactly that module |

Each case prepares a throwaway project with `scaffold.sh` (artifact from `project/`, contract
and earlier rounds from `setup.json`, written through the core, see `evals/_lib/scaffold.py`).
Most graders read `.judicata/ledger.jsonl` after the run, so they are deterministic; one LLM
grader per case reads the final answer. The evals cost tokens and do not run in CI. From the
repository root:

```
claude plugin eval . --scaffold --allow-tools Bash --trust-plugin --runs 1 --no-publish
```

`--scaffold` runs the case scripts, `--allow-tools Bash` lets the skills call the core
(sandboxed: on Linux this needs `bwrap` and `socat` on `PATH`), `--case '<name glob>'` runs
one case, `--ablation none` skips the baseline. Results land in `evals/results/` (ignored by
Git).
