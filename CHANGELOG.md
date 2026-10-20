# Changelog

All notable changes to judicata. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). The version in `.claude-plugin/plugin.json` is the
version users get: Claude Code updates the plugin only when it goes up.

## [Unreleased]

- Stop gate, warn mode: at the end of every turn a Stop hook (`hook stop`, Claude Code and Codex) warns in one line, without stopping the agent, when the latest review of the session for a contract did not reach a final result or when files in a contract's scope changed in the session after its last finished review and no review ran since. Silent in sessions that touched no contract, without `.judicata/` and on any error. `gate = "block"` still only warns. The SessionStart hook records the session start in the state directory for sessions without a transcript.
- Contract format 0.2: `[stop] gate` (`warn` by default, or `block`), a `[verifier]` table with `model` (default `inherit`) and `max_runs` (default 5), and the check kind `references` with `code_paths` (default off). Contracts from 0.1 load unchanged, keep their recorded version and get the defaults; `contract show` and the interview show the new fields. Until the references check ships, references criteria stay pending with that reason.
- Reviewer quotes are checked strictly: a quote must start at the cited line of the cited file (only whitespace and line breaks may differ). A quote that is elsewhere or nowhere is rejected like any invalid reviewer answer, and the reason names the lines where it does start. Before, a quote within 5 lines was accepted and moved to its line.
- Line numbers of reviewer places count line breaks only, as the reviewer's Read tool and the pattern check do; a form feed or a Unicode line separator no longer starts a new line. In a file with such characters a decided finding may come back to you once.
- Plain skill installs (`npx skills add philippmelikidis/judicata`) now get `judicata-review`, `judicata-contract` and `judicata-status`, so the skills no longer collide with commands or other skills of the host. They are generated from the plugin skills and checked in CI; the plugin keeps `judicata:review`, `judicata:contract` and `judicata:status` without duplicates in Claude Code and Codex. The core names the skills the way your install shows them (`/judicata-review` in plain installs, `/judicata:review` in the plugin). If you installed the plain skills of 0.1, remove the old `review`, `contract` and `status` folders after updating.
- Claude Code: a SubagentStop hook checks the answer of judicata's reviewer against the output schema and sends the reviewer back once with the exact problems; a second invalid answer goes to the core, which rejects it as before. Other sub-agents are never touched; Codex keeps the core check (all its sub-agents have the same type).
- `review submit` rejects a reviewer answer with a number longer than Python reads as invalid output instead of failing with an internal error.

## [0.1.0] - 2026-10-19

First public release.

- Contract interview with templates (code, docs, prose); the contract is frozen and changes only on request, as a new version with a visible diff.
- Three checks: command (exit code), pattern (regular expressions, word and line limits), judgment (two independent reviewers with a fresh context; disagreement is decided by the user once).
- Findings outside the contract with addresses that survive moved lines (Python class and function, Markdown heading and paragraph), fingerprints and consensus.
- Decisions (fix, reject, accept the risk) in a ledger in the repository; new sessions respect them, a changed place brings a finding back once.
- Honest stop rule: done, done with accepted risks, required criteria not met (explicit confirmation to finish), not converged, round limit.
- Scope and focus from plain language, several contracts per repository, safe merges of the ledger across branches, works without Git.
- Security: commands of foreign contracts need a local approval; text from the repository is treated as data, cleaned and checked for prompt injection.
- Hosts: Claude Code and Codex as plugins, other agents (OpenCode, Cursor, ...) as plain skills via `npx skills`.

[Unreleased]: https://github.com/philippmelikidis/judicata/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/philippmelikidis/judicata/releases/tag/v0.1.0
