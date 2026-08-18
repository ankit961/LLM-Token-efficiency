# Step 7 — replicated live A/D experiment (design)

**Purpose.** Everything through Step 6.1 is *mechanical*: how much the reducer compresses (R_paired)
and how much inline evidence it drops (line-recall). None of it prices the **end-to-end** question:
once the agent can react — expand a `result://` handle, re-Read, re-search, or take a different
path — does the compression turn into fewer total tokens and turns, or does recovery eat the saving?
Step 7 measures that directly on live `claude -p` sessions.

## Arms (frontier-selected — see docs/step6.1 §4 and `corpus/pareto_frontier.py`)

| arm | budget | floor | rationale |
|---|---:|---:|---|
| **A_native** | — | — | native output + reducer hook in **observe** (equal instrumentation, no reduction) |
| **D1_b256_f125** | 256 | 125 | **dominates the shipped (256,400) on both axes** (more capture *and* more inline evidence) — the safe win |
| **D2_b128_f125** | 128 | 125 | the frontier **knee**: ~2× capture at a small line-recall cost |

Graph ranking is **off** in every arm (Step 6.1: no line-level benefit; `promoted_needed = 0`).
`(64,125)` is deliberately excluded as a default — it strips ~90% of inline evidence for only +0.11
capture over the knee; it can be added later as a downside bound if the knee looks good.

## Design

3 arms × the 4 search-heavy django tasks (`10554, 11138, 12419, 14608`) × **5 reps** = **60
sessions**, headless `claude -p` (sonnet, 600 s cap), each in a fresh worktree at the task's
`base_commit` with a per-run journal, decision log, and `result://` recovery MCP. Reps exist to
average the whole-session trajectory variance that made the single-run Step-5 pilot inconclusive.
The outer loop is rep→task→arm, so partial results cover all arms early and the sweep is resumable
(a run dir with `manifest.json` is re-scored, not re-run). `corpus/step7_live_experiment.py`,
reusing `Step5Runner` + `ClaudeBackend(capture_usage=True)`.

## Metrics

| metric | source | what it answers |
|---|---|---|
| **T_total** = input + output + cache-creation + cache-read | `claude -p --output-format json` usage | the real billed token cost — the headline |
| **turns** (`num_turns`) | same | did reduction change how many steps resolution took |
| **native_rereads** | journal (`file` Read of an already-read path) | did aggressive reduction make the agent re-open files |
| **result_expansions** | transcript (`context_expand` calls) | the *direct* price of the `named_without_match_line` deficit |
| **re_searches** (`exact_search_repeat_count`) | decision-log fingerprints | same query re-run (recovery-by-research) |
| effective_read_tokens, reductions_enforced, non_beneficial, wall_time_s | journal + decision log | mechanism sanity, as in the pilot |

**Task success is DEFERRED.** SWE-bench grading needs the docker/linux harness (unavailable here);
the frozen specs carry only *hashes* of `FAIL_TO_PASS`. Every arm's patch is saved per run
(`agent.patch`) for a later grading pass. Step 7 reports efficiency now, success later — it does not
claim a quality result it did not measure.

## Reading the result

The decisive comparison is **paired within task**: `D1 − A` and `D2 − A` on `T_total` and `turns`,
averaged over 5 reps, per task, then across tasks. A win is *lower* T_total / turns at *equal-or-
better* success (graded later). If `result_expansions`/`native_rereads`/`re_searches` climb under D2
but not D1, that localizes the recovery cost to the knee's lower budget — the exact trade Step 6.1
predicted. Trajectory variance is still real at 5 reps; this is a signal-seeking experiment, not a
final production sign-off.

## Preflight (before any quota)

1. `preflight_or_raise()` — refuse to run enforcing arms unless the live client version is confirmed
   for output replacement (else D1/D2 would reduce nothing).
2. A short dry run (A + D1 on one task) to validate that usage/turns are captured, the transcript is
   located, and D1 actually fires reductions — before committing the full 60.
