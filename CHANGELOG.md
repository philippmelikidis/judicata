# Changelog

All notable changes to judicata. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/). The version in `.claude-plugin/plugin.json` is the
version users get: Claude Code updates the plugin only when it goes up.

## [Unreleased]

- Contract format 0.2: `[stop] gate` (`warn` by default, or `block`), a `[verifier]` table with `model` (default `inherit`) and `max_runs` (default 5), and the check kind `references` with `code_paths` (default off). Contracts from 0.1 load unchanged, keep their recorded version and get the defaults; `contract show` and the interview show the new fields.
- References check: a `references` criterion checks without a model that relative Markdown links, anchors (GitHub heading slugs), images and citation keys (`[@key]`, `\cite{key}` against the `.bib` files of the project) exist; external URLs are ignored, code blocks and inline code are not read as links. With `code_paths = true` inline code spans that are a path with a slash and a file extension are checked relative to the project root. Each broken reference is reported with file, line, reference and reason. The docs template proposes it with `code_paths`, the prose template without.

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
