# Corpus plan v1 — the frozen task list

**Status: SCAFFOLDED — NOT LOCKED, NOT EXECUTABLE.** The mechanically predetermined parts are filled
(run_order, block/within-block position, category via a preregistered blocked permutation, task-spec
slot path, and the runtime/schema freeze stamps). Only the four task-authoring fields — `task_id`,
`task_spec_sha256`, `repo_id`, `base_commit_sha` — remain `UNRESOLVED` in every row, pending the
authored task specs. The plan is LOCKED only when all 50 rows are concrete, all 50 spec hashes exist,
`plan_sha256` is computed, and status flips to LOCKED in a dedicated preregistration commit **before
task 1**. See `docs/corpus-protocol.md` for the admission/analysis rules.

## Two independent immutable objects — do NOT conflate them

    Experiment = (Runtime Snapshot, Preregistered Plan)

- **Runtime Snapshot** = `obs-runtime-3a-v1` → `484f4b35be854cd13966f84a2b6982c07eda227d`. This is the frozen executable under
  test. The corpus runner MUST `git checkout obs-runtime-3a-v1` (it resolves to `484f4b3`),
  never "whatever `main` currently is". Committing this scaffold and the later task specs will
  naturally advance `main` beyond `484f4b3` — that is expected and does NOT change the
  runtime under test.
- **Preregistered Plan** = this file at its lock commit (`plan_lock_commit_sha`, a LATER commit than
  the runtime SHA), identified by tag `corpus-plan-v1` when locked.

## Freeze stamps

| field | value |
|---|---|
| observation_runtime_sha | `484f4b35be854cd13966f84a2b6982c07eda227d` |
| observation_runtime_tag | `obs-runtime-3a-v1` |
| hook_schema | `0.3.0` |
| report_schema | `label-report-0.2.0` |
| run_count | `50` |
| blocks | `10` |
| runs_per_block | `5` |
| ordering_method | `blocked permutation (10 blocks × 5 categories; each block = one full permutation, so exactly 10 runs/category)` |
| ordering_seed | `20260814` |
| ordering_algorithm | see below (deterministic; committed order is authoritative) |
| plan_lock_commit_sha | `UNRESOLVED` (a LATER commit than observation_runtime_sha) |
| plan_sha256 | `UNRESOLVED` (computed only when all 50 rows are concrete) |
| locked_by | `UNRESOLVED` |
| locked_at | `UNRESOLVED` |

## Ordering algorithm (deterministic — recorded so it never regenerates by guess)

Category order confounds are avoided by a **blocked** design: 10 blocks, each block contains every
category exactly once, so each category is spread across the whole experiment (not run in ten
identical cycles). The permutation was generated ONCE from the recorded seed and frozen below; the
table is authoritative even if a future interpreter differs.

    CATS = ['navigation_debugging', 'local_bug_fix', 'multi_file_feature', 'refactor', 'tests_config']
    rng  = random.Random(20260814)          # Python stdlib; str/int seed is deterministic across CPython 3.x
    for block in 1..10:
        perm = shuffle(copy(CATS), rng)   # one full permutation per block
        for within in 1..5: assign category perm[within-1]
    run_order = (block-1)*5 + within

## The 50 rows

`task_spec_path` is the immutable slot each task's spec will occupy (`corpus/specs/run-NN.md`,
authored later, one per run). The four `UNRESOLVED` fields are filled only when the concrete tasks are
authored, in the single plan-lock commit.

| run_order | block_id | within_block_order | task_category | task_spec_path | task_id | task_spec_sha256 | repo_id | base_commit_sha |
|---:|---:|---:|---|---|---|---|---|---|
| 1 | 1 | 1 | tests_config | `corpus/specs/run-01.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 2 | 1 | 2 | refactor | `corpus/specs/run-02.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 3 | 1 | 3 | local_bug_fix | `corpus/specs/run-03.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 4 | 1 | 4 | navigation_debugging | `corpus/specs/run-04.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 5 | 1 | 5 | multi_file_feature | `corpus/specs/run-05.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 6 | 2 | 1 | local_bug_fix | `corpus/specs/run-06.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 7 | 2 | 2 | navigation_debugging | `corpus/specs/run-07.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 8 | 2 | 3 | multi_file_feature | `corpus/specs/run-08.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 9 | 2 | 4 | refactor | `corpus/specs/run-09.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 10 | 2 | 5 | tests_config | `corpus/specs/run-10.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 11 | 3 | 1 | multi_file_feature | `corpus/specs/run-11.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 12 | 3 | 2 | tests_config | `corpus/specs/run-12.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 13 | 3 | 3 | refactor | `corpus/specs/run-13.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 14 | 3 | 4 | local_bug_fix | `corpus/specs/run-14.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 15 | 3 | 5 | navigation_debugging | `corpus/specs/run-15.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 16 | 4 | 1 | refactor | `corpus/specs/run-16.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 17 | 4 | 2 | navigation_debugging | `corpus/specs/run-17.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 18 | 4 | 3 | multi_file_feature | `corpus/specs/run-18.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 19 | 4 | 4 | tests_config | `corpus/specs/run-19.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 20 | 4 | 5 | local_bug_fix | `corpus/specs/run-20.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 21 | 5 | 1 | refactor | `corpus/specs/run-21.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 22 | 5 | 2 | navigation_debugging | `corpus/specs/run-22.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 23 | 5 | 3 | multi_file_feature | `corpus/specs/run-23.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 24 | 5 | 4 | tests_config | `corpus/specs/run-24.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 25 | 5 | 5 | local_bug_fix | `corpus/specs/run-25.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 26 | 6 | 1 | local_bug_fix | `corpus/specs/run-26.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 27 | 6 | 2 | navigation_debugging | `corpus/specs/run-27.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 28 | 6 | 3 | multi_file_feature | `corpus/specs/run-28.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 29 | 6 | 4 | tests_config | `corpus/specs/run-29.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 30 | 6 | 5 | refactor | `corpus/specs/run-30.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 31 | 7 | 1 | refactor | `corpus/specs/run-31.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 32 | 7 | 2 | navigation_debugging | `corpus/specs/run-32.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 33 | 7 | 3 | local_bug_fix | `corpus/specs/run-33.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 34 | 7 | 4 | multi_file_feature | `corpus/specs/run-34.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 35 | 7 | 5 | tests_config | `corpus/specs/run-35.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 36 | 8 | 1 | refactor | `corpus/specs/run-36.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 37 | 8 | 2 | tests_config | `corpus/specs/run-37.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 38 | 8 | 3 | local_bug_fix | `corpus/specs/run-38.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 39 | 8 | 4 | multi_file_feature | `corpus/specs/run-39.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 40 | 8 | 5 | navigation_debugging | `corpus/specs/run-40.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 41 | 9 | 1 | local_bug_fix | `corpus/specs/run-41.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 42 | 9 | 2 | tests_config | `corpus/specs/run-42.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 43 | 9 | 3 | refactor | `corpus/specs/run-43.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 44 | 9 | 4 | navigation_debugging | `corpus/specs/run-44.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 45 | 9 | 5 | multi_file_feature | `corpus/specs/run-45.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 46 | 10 | 1 | local_bug_fix | `corpus/specs/run-46.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 47 | 10 | 2 | multi_file_feature | `corpus/specs/run-47.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 48 | 10 | 3 | refactor | `corpus/specs/run-48.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 49 | 10 | 4 | tests_config | `corpus/specs/run-49.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |
| 50 | 10 | 5 | navigation_debugging | `corpus/specs/run-50.md` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` | `UNRESOLVED` |

**Category counts (by construction):** navigation_debugging=10, local_bug_fix=10,
multi_file_feature=10, refactor=10, tests_config=10.

## Not permitted

- Editing a row after any run's labels are observed.
- Substituting a task because its exploration rate "looks off".
- Regenerating the ordering (the seed + frozen table above are authoritative).
- Recycling a `PILOT / NOT IN CORPUS` run into this table.
- Tuning W to a task's stability. Report whatever W-sensitivity the frozen corpus shows.
- Running against any commit other than `obs-runtime-3a-v1` (`484f4b3`).

## Next (NOT done in this scaffold)

Author 50 immutable task specs at `corpus/specs/run-NN.md` (template: `corpus/specs/_TEMPLATE.md`),
each with an OBJECTIVE success criterion + verification command; compute each `task_spec_sha256`; fill
the four `UNRESOLVED` columns; compute `plan_sha256`; flip status to LOCKED in one preregistration
commit tagged `corpus-plan-v1`. **Task selection is the largest remaining bias source and is being
brought back for review before any specs are authored.**
