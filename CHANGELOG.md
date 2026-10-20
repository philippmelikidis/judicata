# Changelog

All notable changes to judicata. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). The version in `.claude-plugin/plugin.json` is the
version users get: Claude Code updates the plugin only when it goes up.

## [Unreleased]

- Contract format 0.2: `[stop] gate` (`warn` by default, or `block`), a `[verifier]` table with `model` (default `inherit`) and `max_runs` (default 5), and the check kind `references` with `code_paths` (default off). Contracts from 0.1 load unchanged, keep their recorded version and get the defaults; `contract show` and the interview show the new fields. Until the references check ships, references criteria stay pending with that reason.
- Verifier for confirmed blockers: a new `judicata:verifier` agent (fresh context, read tools only) gets one blocker from a judgment criterion or outside the contract with its place, the reviewers' evidence and the results of the approved commands of the round, and tries to refute it. New core commands `verify plan`, `verify submit` (`--self` when the session verifies itself), `verify discard` and `verify reopen`. Verdicts are verified (marked), refuted (only with a checked quote from the scope or a passing approved command with its output, otherwise inconclusive) or inconclusive. Refuted blockers become notes with the reason and no longer hold up done; the user can reopen them. At most `[verifier] max_runs` runs per round, most severe first; the rest are listed as unverified. Command and pattern criteria are never verified. The review skill runs the step, `round close` and `status` report it.
- Verifier, part two: blockers only one reviewer saw go to the verifier before the user; verified ones count like confirmed blockers, refuted ones become notes, only inconclusive ones are put to the user. Verdicts are reused per finding and place (fingerprint, section hash and criterion hash) without a new run, a refutation only while its counter-evidence still holds. Over the limit, `verify plan` says whether to ask (`ask_more`): interactive runs ask once whether to verify the rest (default no) and `verify more` plans them; in block mode (`[stop] gate` or `JUDICATA_GATE=block`, which overrides the contract) nothing is asked and the unverified blockers are listed. Unverified blockers stay blockers.

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
