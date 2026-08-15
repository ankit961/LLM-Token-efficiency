# Corpus plan v2.1 — LOCKED

**Status: LOCKED.** The 50 tasks, their order, and their objective success criteria are frozen. No row
or spec may be edited, substituted, or reordered; W is not tuned to any task. Supersedes the v2 fill
(lexicographic `first-10`, replaced by a SHA-256-ranked pseudo-uniform sample) and the v1 scaffold.

## Two independent immutable objects

    Experiment = (Runtime Snapshot, Preregistered Plan)

- **Runtime Snapshot** = `obs-runtime-3a-v1` → `484f4b35be854cd13966f84a2b6982c07eda227d`. The runner MUST `git checkout obs-runtime-3a-v1`.
- **Preregistered Plan** = this file, `plan_sha256` below, tag `corpus-plan-v1`.

## Freeze stamps

| field | value |
|---|---|
| observation_runtime_sha | `484f4b35be854cd13966f84a2b6982c07eda227d` |
| observation_runtime_tag | `obs-runtime-3a-v1` |
| dataset | `princeton-nlp/SWE-bench_Verified` (split=test), filtered `repo==django/django` |
| dataset_revision | `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` |
| selection_universe_file | `corpus/selection-universe-v1.json` (231 candidates) |
| selection_universe_sha256 | `sha256:adf617753df1fa2413786a1ed7467f24f858fb32787635a230b7c7dc0ee14f30` |
| selection_algorithm | `sha256_rank_v1` — namespace `corpus-v1-select\|<stratum>\|<instance_id>\|<seed>`, lowest 10 |
| selection_seed | `20260815` |
| ordering_algorithm | `sha256_rank` — namespace `corpus-v1-order\|<seed>\|block<b>\|<stratum>` (independent of selection) |
| ordering_seed | `20260815` |
| taxonomy | `fix-shape strata v2 (uniform SWE-bench Verified django, all objective via FAIL_TO_PASS)` |
| hook_schema | `0.3.0` |
| report_schema | `label-report-0.2.0` |
| turn_or_walltime_budget | `40 agent turns OR 20 min wall, whichever first` |
| run_count / blocks / runs_per_block | `50 / 10 / 5` |
| plan_sha256 | `sha256:eb40964c3d1221f1c05a2c73ceab13513e8fb47ad3d1370987975bf7130edd65` |
| plan_lock_commit_sha | → tag `corpus-plan-v1` (this commit) |
| status | `LOCKED` |

`plan_sha256` is computed over the canonical selection content
(`selection_universe_sha256 \| algorithm \| selection_seed \| ordering_seed \| {run_order:stratum:task_id:task_spec_sha256}×50`),
reproducible from the pinned universe + the two seeds, independent of this file's prose or Python's RNG.

## Strata (fix-shape; assigned mechanically from the gold patch, blind to reads)

Deliberately **balanced validation strata**, NOT the natural Django/SWE-bench distribution — chosen
before seeing any ContextRuntime label. Eligible counts `N_h` in the pinned universe:

| stratum | definition | N_h |
|---|---|---:|
| fs1_oneline_1f_le3 | 1 file, ≤3 changed lines | 64 |
| fs2_small_1f_4_6 | 1 file, 4–6 changed lines | 53 |
| fs3_medium_1f_7_15 | 1 file, 7–15 changed lines | 60 |
| fs4_large_1f_16_60 | 1 file, 16–60 changed lines | 22 |
| fs5_multi_ge2f | ≥2 changed files | 32 |

## Preregistered estimand (report BOTH; 10×5 is NOT natural prevalence)

- **Balanced** (primary): θ̂_balanced = (1/5) Σ_h θ̂_h.
- **Django-standardized** (secondary): θ̂_Django = Σ_h (N_h/231)·θ̂_h, with N_h = (64, 53, 60, 22, 32).

Report task-run (macro) alongside read-weighted (micro), with task-cluster bootstrap CIs — never treat
many reads from one task as independent.

## Budget / harness contract (preregistered)

- Wall-time clock **starts immediately before the first agent/model request** — NOT during checkout or
  environment provisioning — and stops when the agent terminates.
- SWE-bench grading runs afterward and **does not consume** the agent budget.
- One "turn" = one headless agent step of the enforced Claude Code primitive (defined in the runner). A
  budget hit is recorded separately as `budget_turns` / `budget_walltime`, **never** conflated with an
  infrastructure failure.

## Objective success

At `base_commit`, apply the agent's final patch; the spec's `FAIL_TO_PASS` go fail→pass AND
`PASS_TO_PASS` stay passing (standard SWE-bench eval). Specs preserve upstream `FAIL_TO_PASS` entries
VERBATIM. One HookJournal DB per run.

## The 50 rows (frozen)

| run_order | block | within | stratum | task_spec_path | task_id | task_spec_sha256 | base_commit_sha |
|---:|---:|---:|---|---|---|---|---|
| 1 | 1 | 1 | fs4_large_1f_16_60 | `corpus/specs/run-01.md` | `django__django-13513` | `sha256:dc1e715a7f42969b…` | `6599608c4d0b` |
| 2 | 1 | 2 | fs5_multi_ge2f | `corpus/specs/run-02.md` | `django__django-15561` | `sha256:fe082d149bd480f6…` | `6991880109e3` |
| 3 | 1 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-03.md` | `django__django-15022` | `sha256:3213e4ef4836848c…` | `e1d673c373a7` |
| 4 | 1 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-04.md` | `django__django-14672` | `sha256:01804ad8f2dabfd2…` | `00ea883ef56f` |
| 5 | 1 | 5 | fs3_medium_1f_7_15 | `corpus/specs/run-05.md` | `django__django-13658` | `sha256:0086d2702142be89…` | `0773837e15bb` |
| 6 | 2 | 1 | fs2_small_1f_4_6 | `corpus/specs/run-06.md` | `django__django-12193` | `sha256:d398054bcd445c76…` | `3fb7c12158a2` |
| 7 | 2 | 2 | fs5_multi_ge2f | `corpus/specs/run-07.md` | `django__django-12406` | `sha256:d884f954ec5cb67d…` | `335c9c94acf2` |
| 8 | 2 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-08.md` | `django__django-11964` | `sha256:a9286d662591a080…` | `fc2b1cc926e3` |
| 9 | 2 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-09.md` | `django__django-11299` | `sha256:f850104a71af3f22…` | `6866c91b638d` |
| 10 | 2 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-10.md` | `django__django-14053` | `sha256:f123770f359ca724…` | `179ee13eb373` |
| 11 | 3 | 1 | fs3_medium_1f_7_15 | `corpus/specs/run-11.md` | `django__django-11239` | `sha256:6b78e3eb190203f9…` | `d87bd29c4f8d` |
| 12 | 3 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-12.md` | `django__django-13590` | `sha256:41b3c273fc6c63b7…` | `755dbf39fcdc` |
| 13 | 3 | 3 | fs4_large_1f_16_60 | `corpus/specs/run-13.md` | `django__django-15382` | `sha256:25349ab682b0bd0a…` | `770d3e6a4ce8` |
| 14 | 3 | 4 | fs5_multi_ge2f | `corpus/specs/run-14.md` | `django__django-11138` | `sha256:b523902f7c31ff73…` | `c84b91b7603e` |
| 15 | 3 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-15.md` | `django__django-12419` | `sha256:674bbd25f7614aef…` | `7fa1a93c6c81` |
| 16 | 4 | 1 | fs2_small_1f_4_6 | `corpus/specs/run-16.md` | `django__django-14771` | `sha256:1e4777742bbe764c…` | `4884a87e0220` |
| 17 | 4 | 2 | fs4_large_1f_16_60 | `corpus/specs/run-17.md` | `django__django-16502` | `sha256:0dc3aee4865e23ba…` | `246eb4836a6f` |
| 18 | 4 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-18.md` | `django__django-13809` | `sha256:f3f4330795aca9f1…` | `bef6f7584280` |
| 19 | 4 | 4 | fs5_multi_ge2f | `corpus/specs/run-19.md` | `django__django-14315` | `sha256:4d8a7d46913fd4b3…` | `187118203197` |
| 20 | 4 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-20.md` | `django__django-11951` | `sha256:d1acba2309feffd8…` | `312049091288` |
| 21 | 5 | 1 | fs1_oneline_1f_le3 | `corpus/specs/run-21.md` | `django__django-15277` | `sha256:7cb2f8bb42aecff7…` | `30613d6a748f` |
| 22 | 5 | 2 | fs5_multi_ge2f | `corpus/specs/run-22.md` | `django__django-13195` | `sha256:5a1ba04541aec5e1…` | `156a2138db20` |
| 23 | 5 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-23.md` | `django__django-13821` | `sha256:c2467efd84e0d18e…` | `e64c1d8055a3` |
| 24 | 5 | 4 | fs3_medium_1f_7_15 | `corpus/specs/run-24.md` | `django__django-14608` | `sha256:7fbc24704df6faf9…` | `7f33c1e22dbc` |
| 25 | 5 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-25.md` | `django__django-15503` | `sha256:d531b7046e386d21…` | `859a87d873ce` |
| 26 | 6 | 1 | fs4_large_1f_16_60 | `corpus/specs/run-26.md` | `django__django-13128` | `sha256:1a8024fe5ca625ea…` | `2d67222472f8` |
| 27 | 6 | 2 | fs3_medium_1f_7_15 | `corpus/specs/run-27.md` | `django__django-12039` | `sha256:7d1c1c35a2e1e41c…` | `58c1acb1d605` |
| 28 | 6 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-28.md` | `django__django-11740` | `sha256:e8de1fc4c45f8a76…` | `003bb34b218a` |
| 29 | 6 | 4 | fs5_multi_ge2f | `corpus/specs/run-29.md` | `django__django-10554` | `sha256:7ff19b07cb8a45ea…` | `14d026cccb14` |
| 30 | 6 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-30.md` | `django__django-16429` | `sha256:261d70e903d44527…` | `6c86495bcee2` |
| 31 | 7 | 1 | fs3_medium_1f_7_15 | `corpus/specs/run-31.md` | `django__django-14765` | `sha256:9e89f067e88f2ddd…` | `4e8121e8e42a` |
| 32 | 7 | 2 | fs1_oneline_1f_le3 | `corpus/specs/run-32.md` | `django__django-13012` | `sha256:12affb232323fba7…` | `22a59c01c00c` |
| 33 | 7 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-33.md` | `django__django-12308` | `sha256:facd8f2138704a9f…` | `2e0f04507b17` |
| 34 | 7 | 4 | fs5_multi_ge2f | `corpus/specs/run-34.md` | `django__django-11333` | `sha256:d12ebabd26119758…` | `55b68de643b5` |
| 35 | 7 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-35.md` | `django__django-11728` | `sha256:ea4101a72adf319c…` | `054578176473` |
| 36 | 8 | 1 | fs5_multi_ge2f | `corpus/specs/run-36.md` | `django__django-13212` | `sha256:d5672390ab950edb…` | `f4e93919e460` |
| 37 | 8 | 2 | fs4_large_1f_16_60 | `corpus/specs/run-37.md` | `django__django-13401` | `sha256:86b29a82929b4f47…` | `453967477e3d` |
| 38 | 8 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-38.md` | `django__django-16901` | `sha256:7e9d676110a4e51d…` | `ee36e101e8f8` |
| 39 | 8 | 4 | fs2_small_1f_4_6 | `corpus/specs/run-39.md` | `django__django-12276` | `sha256:f558979d0bb04f8d…` | `53d8646f799d` |
| 40 | 8 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-40.md` | `django__django-11477` | `sha256:7db6ba211c8ae31a…` | `e28671187903` |
| 41 | 9 | 1 | fs1_oneline_1f_le3 | `corpus/specs/run-41.md` | `django__django-16527` | `sha256:d4546e9deb2f01c3…` | `bd366ca2aeff` |
| 42 | 9 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-42.md` | `django__django-15368` | `sha256:412619dfd5849e3e…` | `e972620ada4f` |
| 43 | 9 | 3 | fs5_multi_ge2f | `corpus/specs/run-43.md` | `django__django-16256` | `sha256:63097a293ff1bd8b…` | `76e37513e22f` |
| 44 | 9 | 4 | fs4_large_1f_16_60 | `corpus/specs/run-44.md` | `django__django-12713` | `sha256:007cd20f522a92f5…` | `5b884d45ac5b` |
| 45 | 9 | 5 | fs3_medium_1f_7_15 | `corpus/specs/run-45.md` | `django__django-16136` | `sha256:78cbb9c535b84653…` | `19e6efa50b60` |
| 46 | 10 | 1 | fs1_oneline_1f_le3 | `corpus/specs/run-46.md` | `django__django-13406` | `sha256:230ac91f85cb3351…` | `84609b320590` |
| 47 | 10 | 2 | fs3_medium_1f_7_15 | `corpus/specs/run-47.md` | `django__django-13449` | `sha256:ecaa6820ee3e8d64…` | `2a55431a5678` |
| 48 | 10 | 3 | fs4_large_1f_16_60 | `corpus/specs/run-48.md` | `django__django-15554` | `sha256:09463785c2ec7759…` | `59ab3fd0e9e6` |
| 49 | 10 | 4 | fs2_small_1f_4_6 | `corpus/specs/run-49.md` | `django__django-12143` | `sha256:f1ebf7c53439d5bc…` | `5573a54d409b` |
| 50 | 10 | 5 | fs5_multi_ge2f | `corpus/specs/run-50.md` | `django__django-13344` | `sha256:ecced1ccb1518c61…` | `e39e727ded67` |

## Scope

One repository (django), one issue-fix distribution — downstream claims stay scoped to Django/SWE-bench
label validity. NOT a population token-savings dataset.

## Not permitted

- Editing a row/spec, substituting a task, or reordering, after lock.
- Regenerating selection/ordering (universe + seeds + this table are authoritative).
- Tuning W to a task's stability. Running against any commit other than `obs-runtime-3a-v1` (`484f4b3`).
