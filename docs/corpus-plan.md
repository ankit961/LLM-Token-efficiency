# Corpus plan v2 — the frozen task list (fix-shape strata)

**Status: FILLED — PENDING FINAL REVIEW, NOT YET LOCKED.** All 50 rows are concrete (task_id,
task_spec_sha256, repo_id, base_commit_sha filled from SWE-bench Verified django) and the 50 immutable
specs exist at `corpus/specs/run-NN.md`. The plan is LOCKED only after this fill is reviewed, then
`plan_sha256` is computed and status flips to LOCKED in a dedicated commit tagged `corpus-plan-v1`.
Supersedes the v1 SCAFFOLD (semantic categories), which was never locked — SWE-bench django cleanly
supports only 2 of those 5 (tests_config structurally impossible, refactor absent), so we switched to
uniform fix-shape strata (all objective via FAIL_TO_PASS, uniform provenance). See
`docs/corpus-selection.md`.

## Two independent immutable objects

    Experiment = (Runtime Snapshot, Preregistered Plan)

- Runtime Snapshot = `obs-runtime-3a-v1` → `484f4b35be854cd13966f84a2b6982c07eda227d` (runner MUST `git checkout obs-runtime-3a-v1`).
- Preregistered Plan = this file at its lock commit (`plan_lock_commit_sha`, a LATER commit).

## Freeze stamps

| field | value |
|---|---|
| observation_runtime_sha | `484f4b35be854cd13966f84a2b6982c07eda227d` |
| observation_runtime_tag | `obs-runtime-3a-v1` |
| taxonomy | `fix-shape strata v2 (uniform SWE-bench Verified django, all objective)` |
| hook_schema | `0.3.0` |
| report_schema | `label-report-0.2.0` |
| dataset | `princeton-nlp/SWE-bench_Verified` (split=test), filtered `repo==django/django` (231 instances) |
| run_count | `50` |
| blocks / runs_per_block | `10 / 5` |
| ordering_method | `blocked permutation (10 blocks × 5 strata; each block = one full permutation ⇒ 10 runs/stratum)` |
| ordering_seed | `20260815` (v2; v1's 20260814 superseded pre-lock) |
| turn_or_walltime_budget | `40 agent turns OR 20 min wall, whichever first` |
| plan_lock_commit_sha | `UNRESOLVED` |
| plan_sha256 | `UNRESOLVED` |
| status | `FILLED — pending review` |

## Strata (fix-shape; assigned mechanically from the gold patch, blind to reads)

| stratum | definition | eligible in django |
|---|---|---|
| fs1_oneline_1f_le3 | 1 source file, ≤3 changed lines (locate-then-minimal-fix) | 64 |
| fs2_small_1f_4_6 | 1 source file, 4–6 changed lines | 53 |
| fs3_medium_1f_7_15 | 1 source file, 7–15 changed lines | 60 |
| fs4_large_1f_16_60 | 1 source file, 16–60 changed lines | 22 |
| fs5_multi_ge2f | ≥2 source files changed | 32 |

Selection: within each stratum, eligible instances sorted by `instance_id`, first 10 taken
(deterministic). Objective success per task = its `FAIL_TO_PASS` go fail→pass AND `PASS_TO_PASS` stay
passing at `base_commit` (standard SWE-bench eval). One HookJournal DB per run.

## The 50 rows

| run_order | block_id | within_block_order | stratum | task_spec_path | task_id | task_spec_sha256 | repo_id | base_commit_sha |
|---:|---:|---:|---|---|---|---|---|---|
| 1 | 1 | 1 | fs4_large_1f_16_60 | `corpus/specs/run-01.md` | `django__django-10973` | `sha256:9ef1d2f8dafc645c…` | `django/django` | `ddb293685235` |
| 2 | 1 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-02.md` | `django__django-11095` | `sha256:44f0ab1b69819aaf…` | `django/django` | `7d49ad76562e` |
| 3 | 1 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-03.md` | `django__django-10999` | `sha256:7632cc1d0623afd1…` | `django/django` | `36300ef336e3` |
| 4 | 1 | 4 | fs5_multi_ge2f | `corpus/specs/run-04.md` | `django__django-10554` | `sha256:4906d5bdd8240556…` | `django/django` | `14d026cccb14` |
| 5 | 1 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-05.md` | `django__django-10097` | `sha256:c83d6bc47ab89a62…` | `django/django` | `b9cf764be62e` |
| 6 | 2 | 1 | fs4_large_1f_16_60 | `corpus/specs/run-06.md` | `django__django-11087` | `sha256:528c133bac8b4b76…` | `django/django` | `8180ffba21bf` |
| 7 | 2 | 2 | fs5_multi_ge2f | `corpus/specs/run-07.md` | `django__django-11138` | `sha256:ad3f71aba7b81c63…` | `django/django` | `c84b91b7603e` |
| 8 | 2 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-08.md` | `django__django-11099` | `sha256:def76579001b3ee9…` | `django/django` | `d26b2424437d` |
| 9 | 2 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-09.md` | `django__django-10880` | `sha256:99fac051c154f1c8…` | `django/django` | `838e432e3e55` |
| 10 | 2 | 5 | fs3_medium_1f_7_15 | `corpus/specs/run-10.md` | `django__django-11141` | `sha256:d709d3f0228e3a60…` | `django/django` | `5d9cf79baf07` |
| 11 | 3 | 1 | fs2_small_1f_4_6 | `corpus/specs/run-11.md` | `django__django-11211` | `sha256:1c4b4b530022106d…` | `django/django` | `ba726067604c` |
| 12 | 3 | 2 | fs4_large_1f_16_60 | `corpus/specs/run-12.md` | `django__django-11149` | `sha256:c1e24231c7cf151a…` | `django/django` | `e245046bb6e8` |
| 13 | 3 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-13.md` | `django__django-11206` | `sha256:b70d92f87101dacb…` | `django/django` | `571ab44e8a89` |
| 14 | 3 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-14.md` | `django__django-10914` | `sha256:760500e07a37761e…` | `django/django` | `e7fd69d051ea` |
| 15 | 3 | 5 | fs5_multi_ge2f | `corpus/specs/run-15.md` | `django__django-11333` | `sha256:c14c5d5c42926b15…` | `django/django` | `55b68de643b5` |
| 16 | 4 | 1 | fs2_small_1f_4_6 | `corpus/specs/run-16.md` | `django__django-11740` | `sha256:a60d6e082271ae7b…` | `django/django` | `003bb34b218a` |
| 17 | 4 | 2 | fs3_medium_1f_7_15 | `corpus/specs/run-17.md` | `django__django-11239` | `sha256:a01bd49c04c01bd6…` | `django/django` | `d87bd29c4f8d` |
| 18 | 4 | 3 | fs5_multi_ge2f | `corpus/specs/run-18.md` | `django__django-11400` | `sha256:5c2cc4b9f567723b…` | `django/django` | `1f8382d34d54` |
| 19 | 4 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-19.md` | `django__django-11066` | `sha256:df33a3a5e5062d8d…` | `django/django` | `4b45b6c8e4d7` |
| 20 | 4 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-20.md` | `django__django-11276` | `sha256:aacd0268ae42775d…` | `django/django` | `28d5262fa331` |
| 21 | 5 | 1 | fs3_medium_1f_7_15 | `corpus/specs/run-21.md` | `django__django-11265` | `sha256:0d42d5937508e5ea…` | `django/django` | `21aa2a5e785e` |
| 22 | 5 | 2 | fs1_oneline_1f_le3 | `corpus/specs/run-22.md` | `django__django-11119` | `sha256:6fc4530a86e36e5e…` | `django/django` | `d4df5e1b0b1c` |
| 23 | 5 | 3 | fs5_multi_ge2f | `corpus/specs/run-23.md` | `django__django-11532` | `sha256:c6a15616a2af480a…` | `django/django` | `a5308514fb4b` |
| 24 | 5 | 4 | fs2_small_1f_4_6 | `corpus/specs/run-24.md` | `django__django-11790` | `sha256:dda1958a73fc0755…` | `django/django` | `b1d6b35e146a` |
| 25 | 5 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-25.md` | `django__django-11551` | `sha256:9fae728ef452ea3e…` | `django/django` | `7991111af120` |
| 26 | 6 | 1 | fs5_multi_ge2f | `corpus/specs/run-26.md` | `django__django-11734` | `sha256:ba0e4b4b73a3109f…` | `django/django` | `999891bd80b3` |
| 27 | 6 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-27.md` | `django__django-12143` | `sha256:273bcb2c6770482d…` | `django/django` | `5573a54d409b` |
| 28 | 6 | 3 | fs4_large_1f_16_60 | `corpus/specs/run-28.md` | `django__django-11728` | `sha256:894cfb3a32427e38…` | `django/django` | `054578176473` |
| 29 | 6 | 4 | fs3_medium_1f_7_15 | `corpus/specs/run-29.md` | `django__django-11292` | `sha256:30ae2a532ad6af75…` | `django/django` | `eb16c7260e57` |
| 30 | 6 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-30.md` | `django__django-11133` | `sha256:9db7f38c534dcbef…` | `django/django` | `879cc3da6249` |
| 31 | 7 | 1 | fs5_multi_ge2f | `corpus/specs/run-31.md` | `django__django-11885` | `sha256:2a90bbed0783daf7…` | `django/django` | `04ac9b45a344` |
| 32 | 7 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-32.md` | `django__django-12193` | `sha256:e1e787284690f60c…` | `django/django` | `3fb7c12158a2` |
| 33 | 7 | 3 | fs4_large_1f_16_60 | `corpus/specs/run-33.md` | `django__django-12050` | `sha256:f0658502e0ca61db…` | `django/django` | `b93a0e34d9b9` |
| 34 | 7 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-34.md` | `django__django-11163` | `sha256:8e764829eb9a783d…` | `django/django` | `e6588aa4e793` |
| 35 | 7 | 5 | fs3_medium_1f_7_15 | `corpus/specs/run-35.md` | `django__django-11433` | `sha256:e61bdb6db11e170b…` | `django/django` | `21b1d239125f` |
| 36 | 8 | 1 | fs5_multi_ge2f | `corpus/specs/run-36.md` | `django__django-12155` | `sha256:0ca90a142811e5af…` | `django/django` | `e8fcdaad5c42` |
| 37 | 8 | 2 | fs3_medium_1f_7_15 | `corpus/specs/run-37.md` | `django__django-11749` | `sha256:8ad701aaa0282015…` | `django/django` | `350123f38c2b` |
| 38 | 8 | 3 | fs2_small_1f_4_6 | `corpus/specs/run-38.md` | `django__django-12276` | `sha256:05216820da7de225…` | `django/django` | `53d8646f799d` |
| 39 | 8 | 4 | fs4_large_1f_16_60 | `corpus/specs/run-39.md` | `django__django-12713` | `sha256:ea89b1a1e99c4948…` | `django/django` | `5b884d45ac5b` |
| 40 | 8 | 5 | fs1_oneline_1f_le3 | `corpus/specs/run-40.md` | `django__django-11179` | `sha256:ec08e5f1368e3527…` | `django/django` | `19fc6376ce67` |
| 41 | 9 | 1 | fs5_multi_ge2f | `corpus/specs/run-41.md` | `django__django-12325` | `sha256:e2a72257df56de50…` | `django/django` | `29c126bb3495` |
| 42 | 9 | 2 | fs2_small_1f_4_6 | `corpus/specs/run-42.md` | `django__django-12308` | `sha256:30160a9127f00686…` | `django/django` | `2e0f04507b17` |
| 43 | 9 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-43.md` | `django__django-11815` | `sha256:f8a3f21998dd97b8…` | `django/django` | `e02f67ef2d03` |
| 44 | 9 | 4 | fs1_oneline_1f_le3 | `corpus/specs/run-44.md` | `django__django-11299` | `sha256:b47fd3327b475f88…` | `django/django` | `6866c91b638d` |
| 45 | 9 | 5 | fs4_large_1f_16_60 | `corpus/specs/run-45.md` | `django__django-13128` | `sha256:ee752155d0f985a4…` | `django/django` | `2d67222472f8` |
| 46 | 10 | 1 | fs2_small_1f_4_6 | `corpus/specs/run-46.md` | `django__django-12858` | `sha256:d7ed8b1d98ea4f0c…` | `django/django` | `f2051eb8a7fe` |
| 47 | 10 | 2 | fs1_oneline_1f_le3 | `corpus/specs/run-47.md` | `django__django-11451` | `sha256:0d82512d034f4221…` | `django/django` | `e065b293878b` |
| 48 | 10 | 3 | fs3_medium_1f_7_15 | `corpus/specs/run-48.md` | `django__django-11820` | `sha256:0f1f1da9745ec3c8…` | `django/django` | `c2678e49759e` |
| 49 | 10 | 4 | fs4_large_1f_16_60 | `corpus/specs/run-49.md` | `django__django-13346` | `sha256:07123bf9a6bd3c73…` | `django/django` | `9c92924cd5d1` |
| 50 | 10 | 5 | fs5_multi_ge2f | `corpus/specs/run-50.md` | `django__django-12406` | `sha256:aa05e23c0bcdfcd5…` | `django/django` | `335c9c94acf2` |

**Stratum counts (by construction):** 10 each.

## Not permitted

- Editing a row or spec after any run's labels are observed.
- Regenerating the ordering (seed 20260815 + this table are authoritative).
- Substituting a task by its exploration rate; tuning W to a task's stability.
- Running against any commit other than `obs-runtime-3a-v1` (`484f4b3`).

## Next (before LOCK)

Review the 50 concrete tasks; then compute `plan_sha256` over this file, set status LOCKED, tag
`corpus-plan-v1`. Then build the run harness and collect.
