# Changelog

All notable changes to judicata. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). The version in `.claude-plugin/plugin.json` is the
version users get: Claude Code updates the plugin only when it goes up.

## [Unreleased]

- Stop gate, block mode: with `gate = "block"` in the contract or `JUDICATA_GATE=block` in the environment the Stop hook keeps the agent from ending its turn, with a reason that names the open blockers or changed files and says what to do (review again, or report honestly that the work is not done). It lets go once the latest review reached a final result and after at most three blocks per session, recording a `gate_released` event in the ledger. `JUDICATA_GATE=warn` turns a block contract back to warnings for a run. Plain skill installs carry the rule in the skills: check the status before saying done and name what is open. README: what blocks, warns or relies on the skill per host, and `codex --sandbox read-only` for repositories you do not trust.
- Stop gate, warn mode: at the end of every turn a Stop hook (`hook stop`, Claude Code and Codex) warns in one line, without stopping the agent, when the latest review of the session for a contract did not reach a final result or when files in a contract's scope changed in the session after its last finished review and no review ran since. Silent in sessions that touched no contract, without `.judicata/` and on any error. The SessionStart hook records the session start in the state directory for sessions without a transcript.
- Contract format 0.2: `[stop] gate` (`warn` by default, or `block`), a `[verifier]` table with `model` (default `inherit`) and `max_runs` (default 5), and the check kind `references` with `code_paths` (default off). Contracts from 0.1 load unchanged, keep their recorded version and get the defaults; `contract show` and the interview show the new fields. Until the references check ships, references criteria stay pending with that reason.

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
