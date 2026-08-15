# Corpus task-selection procedure v2 — uniform SWE-bench fix-shape strata (EXECUTED, not locked)

**Purpose:** make the choice of the 50 tasks fully mechanical and reproducible, so selection carries no
researcher freedom. This procedure was executed to fill `docs/corpus-plan.md` (status FILLED — pending
review, not locked). It supersedes v1 (semantic categories via hybrid sourcing): a feasibility check
showed SWE-bench Verified django cleanly supplies only 2 of those 5 categories — `tests_config` is
structurally impossible (SWE-bench splits the eval test into a separate `test_patch`; the gold patch is
source-only), `refactor` is absent (issue-fix data), and `multi_file_feature` had only 9. Uniform
fix-shape strata avoid hand-curation and give every task a free, objective `FAIL_TO_PASS` check.

- **Repository (single, fixed):** `django/django`.
- **Source:** `princeton-nlp/SWE-bench_Verified` (split=test), filtered `repo=="django/django"` → 231
  instances. Each provides `instance_id`, `base_commit`, gold `patch`, `test_patch`, `FAIL_TO_PASS`,
  `PASS_TO_PASS`, `problem_statement`. **Uniform provenance — no second pool.**
- **Runtime under test:** `obs-runtime-3a-v1` → `484f4b3` (runner checks out the tag).
- **One HookJournal DB per run** (the task run is the unit).

## Fix-shape strata (assigned mechanically from the gold patch, BLIND to reads)

Source file = a changed file counted from the gold `patch` (SWE-bench patches are source-only).
`n_src_files`, `src_lines` = files / (added+removed) lines in the patch. Strata are disjoint and cover
all 231 (single-file with >60 lines = 0 in django). Eligible counts recorded at selection time:

| stratum | definition | eligible |
|---|---|---:|
| `fs1_oneline_1f_le3` | 1 file, ≤3 lines (locate-then-minimal-fix) | 64 |
| `fs2_small_1f_4_6` | 1 file, 4–6 lines | 53 |
| `fs3_medium_1f_7_15` | 1 file, 7–15 lines | 60 |
| `fs4_large_1f_16_60` | 1 file, 16–60 lines | 22 |
| `fs5_multi_ge2f` | ≥2 files | 32 |

These are *fix shapes*, not semantic categories. For **label validity** that is sufficient: the
labeller needs varied read/edit patterns (tiny fix ⇒ heavy navigation; multi-file ⇒ spread edits),
which fix shape provides. No claim is made that a stratum equals a "kind of work".

## Deterministic selection (no peeking)

1. Classify every django instance into its stratum; all five have ≥10 eligible (min 22).
2. Within each stratum, sort eligible instances by `instance_id` (lexicographic); take the **first 10**.
3. Blocked order (10 blocks × 5 strata, one full permutation per block ⇒ 10 runs/stratum) generated
   once from recorded seed **20260815** (`docs/corpus-plan.md`). Each stratum's 10 tasks fill that
   stratum's 10 run_orders in ascending order.

## Objective success (`verification_command`; standard SWE-bench eval)

At `base_commit`, apply the agent's final patch; the instance's `FAIL_TO_PASS` tests must go fail→pass
AND `PASS_TO_PASS` must stay passing. NOT the agent's self-report. Each spec pins `FAIL_TO_PASS` and a
`PASS_TO_PASS_sha256`.

## Task prompt

The verbatim `problem_statement` (the real GitHub issue text) — the only thing the agent sees.

## What was produced (this fill) and what remains

- 50 immutable `corpus/specs/run-NN.md` + filled plan rows + `task_spec_sha256`. **Reviewed before
  lock.**
- NOT yet done: compute `plan_sha256`, flip status LOCKED, tag `corpus-plan-v1`; build the run harness
  (checkout the tag → isolated worktree per `base_commit` → headless agent on `problem_statement` with
  cr-hook wired → SWE-bench eval → one journal + label-report per run); collect.
