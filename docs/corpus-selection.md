# Corpus task-selection procedure v2.1 — uniform SHA256-ranked, pinned universe (LOCKED)

**Purpose:** make the choice of the 50 tasks fully mechanical, uniform, and reproducible from a pinned
universe — so selection carries no researcher freedom and does not depend on any language RNG. This
procedure produced the LOCKED `docs/corpus-plan.md` (`corpus-plan-v1`).

- **Repository (single, fixed):** `django/django`.
- **Source:** `princeton-nlp/SWE-bench_Verified` (split=test, revision pinned in the plan), filtered
  `repo=="django/django"` → 231 instances. Uniform provenance, no second pool.
- **Runtime under test:** `obs-runtime-3a-v1` → `484f4b3` (runner checks out the tag).
- **One HookJournal DB per run** (the task run is the unit).

## Pinned selection universe

All 231 candidates are frozen in `corpus/selection-universe-v1.json` with only selection-relevant
metadata per candidate (`instance_id`, `base_commit_sha`, `n_src_files`, `src_lines`, `stratum`,
`problem_statement_sha256`, `fail_to_pass_sha256`, `pass_to_pass_sha256`) plus `dataset_revision`. The
file's `selection_universe_sha256` is recorded in the plan. Selection is therefore independently
reproducible even if the upstream dataset later changes.

## Fix-shape strata (assigned mechanically from the gold patch, BLIND to reads)

Deliberately **balanced validation strata**, not the natural distribution. Disjoint; cover all 231
(single-file >60 lines = 0 in django).

| stratum | definition | N_h |
|---|---|---:|
| `fs1_oneline_1f_le3` | 1 file, ≤3 lines | 64 |
| `fs2_small_1f_4_6` | 1 file, 4–6 lines | 53 |
| `fs3_medium_1f_7_15` | 1 file, 7–15 lines | 60 |
| `fs4_large_1f_16_60` | 1 file, 16–60 lines | 22 |
| `fs5_multi_ge2f` | ≥2 files | 32 |

These are *fix shapes*, not semantic categories — sufficient for **label validity** (varied read/edit
patterns), with no claim that a stratum equals a "kind of work".

## Deterministic uniform selection (SHA256 rank — no RNG, no lexicographic bias)

For each eligible instance in a stratum, score
`s(i) = SHA256("corpus-v1-select|" + stratum + "|" + instance_id + "|20260815")`; sort ascending by
`s(i)`; take the **lowest 10**. This is a reproducible pseudo-uniform sample that does not favor any
portion of the ticket-number sequence (unlike `sort(instance_id) → first 10`, which it replaces).

## Independent blocked ordering (separate namespace)

For each block `b` in 1..10, order the 5 strata by
`SHA256("corpus-v1-order|20260815|block" + b + "|" + stratum)` → one full permutation per block ⇒ 10
runs/stratum. A distinct namespace makes **membership and execution order independent**. Each stratum's
10 tasks fill its 10 run_orders (ascending), assigned in selection-score order.

## Objective success (standard SWE-bench eval)

At `base_commit`, apply the agent's final patch; `FAIL_TO_PASS` go fail→pass AND `PASS_TO_PASS` stay
passing. NOT the agent's self-report. **Upstream `FAIL_TO_PASS` entries are preserved VERBATIM** — some
read like prose (e.g. `django__django-10973`'s "SIGINT is ignored…") but are genuine upstream
evaluation directives; the generator must never "clean" them.

## Reproducibility checks (all pass at lock)

50 unique tasks; exactly 10/stratum; every block has all 5 strata; selection reproduces from the
universe + seed; `selection_universe_sha256` reproduces; `plan_sha256` reproduces from the canonical
content; runtime tag still → `484f4b3`.

## Not done here (next)

Build the run harness (checkout the tag → isolated worktree per `base_commit` → headless agent on
`problem_statement` with cr-hook wired to a per-run journal → SWE-bench eval for `task_outcome` →
journal + label-report per run, per the budget/harness contract in the plan), then collect.
