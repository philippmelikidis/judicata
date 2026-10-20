# Changelog

All notable changes to judicata. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). The version in `.claude-plugin/plugin.json` is the
version users get: Claude Code updates the plugin only when it goes up.

## [Unreleased]

- Claude Code: a SubagentStop hook checks the answer of judicata's reviewer against the output schema and sends the reviewer back once with the exact problems; a second invalid answer goes to the core, which rejects it as before. Other sub-agents are never touched; Codex keeps the core check (all its sub-agents have the same type).

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
