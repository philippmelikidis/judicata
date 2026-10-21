# Measurement

For maintainers. `measure/measure.py` runs judicata and plain Claude Code over the same corpus with known
truth and writes the numbers below. It is not shipped, not part of the version check and does not run in CI
(it costs tokens); `tests/test_measure.py` only runs it on a tiny corpus with a fake `claude`.

## What is compared

Every case runs in two arms with the same prompts on the same throwaway project:

- **judicata**: Claude Code with this checkout loaded via `--plugin-dir` (skills, reviewer agent, hook).
- **plain Claude**: the same Claude Code without the plugin, on the artifact without `.judicata/`.

Both arms run `claude -p` with `--setting-sources project,local --strict-mcp-config`, so your user settings,
installed plugins and MCP servers stay out (built-in skills stay in), in `dontAsk` mode. Tools: Read, Glob,
Grep, Agent, Skill and Bash; Edit, Write, NotebookEdit and web tools are denied. Bash runs in Claude Code's
sandbox (bubblewrap on Linux, `bwrap` and `socat` must be on `PATH`): writes only below the project, no
network. The sandbox approves simple commands; commands it cannot analyse (a heredoc, a command name taken
from a variable) are denied unless `--allow-bash` allows every shell command, still inside the sandbox, as
`claude plugin eval --allow-tools Bash` does. The skills pass reviewer answers to the core with a heredoc, so
without `--allow-bash` the judicata arm often cannot record a round (see the first runs below).

The corpus:

- **Eval cases** (`evals/<case>/`, names `eval/<case>`): the judicata arm gets the project from the case's
  scaffold (artifact, contract, earlier rounds and decisions), the plain arm only the artifact. Truth per case
  in `measure/evals.json` (expected done, seeded bugs, points decided earlier); `"measure": false` leaves a case
  out (`07-gate-block`: its prompt asks for an edit, which the measurement forbids). One fresh session per trial.
- **Seeded cases** (`measure/seeded/<case>/`, names `seeded/<case>`): a small clean project plus mutations in
  `case.json`. Each trial applies `pick` of the bug mutations (chosen by `--seed`) and every decoy (code that
  looks odd but is intended, with the user's reason). Generated in a throwaway folder at run time, never
  committed. Then rounds: a fresh review session; the user's decisions in the same session (every point that
  is not a seeded bug is rejected, a decoy with its reason); the reported seeded bugs are fixed and committed;
  the next round starts in a fresh session. This ends when a review says done without a blocker or after
  `--max-rounds`.

Every review prompt asks for a `findings` block (JSON: done, findings with file, line, severity, summary) at
the end of the reply. The metrics come only from these blocks, the same way for both arms. A finding matches a
known point when it names the same file and a line within 3 lines (without a line: keywords of the point);
a finding near several points counts for the closest one only. A trial whose review failed, ran out of budget
or had no valid findings block is left out of the metrics and counted as left out.

## Metrics

- **Re-raise rate**: points decided earlier that a fresh session reports as open again, at any severity.
  Decided means rejected by the user in an earlier session of the same trial (both arms), or in the scaffold of
  an eval case (judicata arm only: the plain arm never heard of that decision). A decision counts only if its
  session turn finished.
- **Converged within N rounds**: seeded trials whose review said done without a blocker within `--max-rounds`,
  with the mean number of rounds. "Said done with seeded bugs left" counts those of them that still had a
  seeded bug, so missing bugs never looks like convergence.
- **Blocker precision and recall**: blockers of the first review against the seeded bugs (precision: blockers
  that are seeded bugs; recall: seeded bugs reported as blockers). "Recall, any severity" also counts
  should-fix and notes.
- **Done as expected**: eval cases whose reply says done exactly when the truth says so.
- Indicators: replies without a findings block, trials left out, sessions in which the judicata skill fired,
  cost per arm.

## Why `claude -p` and not `claude plugin eval`

`claude plugin eval --ablation with-without` runs one prompt per case in one session and grades it. It cannot
hold a conversation (no second turn with the user's decisions) and cannot start a second, fresh session on the
same project, so it cannot measure re-raises over two sessions; a scaffold can only play session 1 in advance
through the core, which helps the judicata arm only. The evals stay the acceptance tests of the plugin; the
measurement reuses their scaffolds and drives `claude -p` itself: `--resume` for the decisions in session 1,
a new session for the next review.

## Cost cap

Every `claude -p` call gets `--max-budget-usd`, the smaller of `--max-run-usd` (default 1.50) and what is left of
`--max-cost-usd` (default 5.00). No call starts once the total reached the cap; the run then stops with exit 2
and the summary says "stopped at the cost cap" (a trial cut by the cap is left out). A call that reports no cost
counts with its whole budget. `--dry-run` shows the trials and the most sessions they can take.

## Running it

```
python3 measure/measure.py --raw-dir <folder outside this repository> --max-cost-usd 5 [--allow-bash] [--case 'seeded/*'] [--arm judicata] [--model sonnet] [--trials 1] [--max-rounds 3]
```

The summary goes into this file (`--summary`), newest first, with date, judicata version, Claude Code version,
model and case count. Raw results (stream of every session, replies, per trial details in `results.json`) go to
`--raw-dir`, which must lie outside this repository; the run id in the summary names its folder.
`--plugin-dir` measures another checkout (for example a release tag in a worktree), `--keep-temp` keeps the
throwaway projects.

Baseline for 0.1 (not run yet, 8 trials and at most 16 sessions):

```
python3 measure/measure.py --allow-bash --case 'eval/01-*' --case 'eval/03-*' --case 'seeded/*' --max-rounds 2 --max-cost-usd 10 --raw-dir <folder outside this repository>
```

## Limits

- Small corpus, one trial per case and arm by default: the numbers show a direction, not a significant result.
- The plain arm gets no notes file and may not edit files, so whatever plain Claude keeps across sessions is
  what a user gets by default. The judicata arm keeps its ledger.
- Matching by file and line is a heuristic: a finding three lines away from a seeded bug counts as that bug.
- The prompts ask for a findings block, which changes the reply format of both arms a little.
- The judicata arm sees a contract; the plain arm gets no comparable definition of done.

## Results

The first two runs (2026-10-19, one seeded case, Sonnet, 0.48 and 0.53 USD) only show that the script works,
they are no baseline: without `--allow-bash` the judicata arm could not submit the reviewer answer (heredoc
denied) and so recorded no decision, and the first run's prompt ("do not change any files") kept it from
writing its ledger at all. The baseline for 0.1 is still to run with the command above.

<!-- measurement results, newest first -->

### 2026-10-19 (run 20261019-213816-56ab6b)

- judicata 0.1.0 (v0.1.0-3-g643cf4d), Claude Code 2.1.303, model: claude-opus-5-5
- cases: 4 (eval/01-rejected-finding, eval/03-required-blocker, seeded/inventory, seeded/invoice), trials per case and arm: 1, max rounds: 2, seed: judicata
- cost: 4.50 USD of a cap of 10.00 USD, 16 sessions

| Metric | judicata | plain Claude |
| --- | --- | --- |
| Re-raise rate | 0 of 3 (0 %) | 4 of 4 (100 %) |
| Converged within 2 rounds | 0 of 2 | 1 of 2, mean 2.0 rounds |
| Said done with seeded bugs left | n/a | 0 of 1 |
| Blocker precision | 4 of 4 (100 %) | 4 of 4 (100 %) |
| Blocker recall | 4 of 5 (80 %) | 4 of 5 (80 %) |
| Recall, any severity | 5 of 5 (100 %) | 5 of 5 (100 %) |
| Done as expected (eval cases) | 2 of 2 (100 %) | 2 of 2 (100 %) |
| Replies without a findings block | 0 of 6 | 0 of 6 |
| Trials left out (failed or no block) | 0 of 4 | 0 of 4 |
| Skill fired | 6 of 6 sessions | n/a |
| Cost (USD) | 3.50 | 1.00 |
