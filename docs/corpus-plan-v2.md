# Corpus plan v2 — LOCKED (v0.4 observation runtime)

**Status: LOCKED.** The task SAMPLE is byte-identical to `corpus-plan-v1` — same universe, same seeds,
same 50 `corpus/specs/run-NN.md`, same order, same `plan_sha256`. The ONLY change is the observation
runtime: the pilot showed the v0.3 layer was blind to agent shell-navigation, so v2 binds the same 50
tasks to **`hook_schema 0.4.0`** (representation-typed shell). Selection is NEVER reopened — changing
instrumentation is not a reason to redraw tasks.

    Experiment_v2 = (Runtime Snapshot v2, Preregistered Plan v1-sample)

## Freeze stamps

| field | value |
|---|---|
| observation_runtime_sha | `43ecd95d8f9240a345249525bd6d884f0a768c69` |
| observation_runtime_tag | `obs-runtime-3a-v2` |
| hook_schema | `0.4.0` (representation-typed shell; search/path/file/git_blob + execution) |
| report_schema | `label-report-0.2.0` |
| selection_universe_file | `corpus/selection-universe-v1.json` (unchanged) |
| selection_universe_sha256 | `sha256:adf617753df1fa2413786a1ed7467f24f858fb32787635a230b7c7dc0ee14f30` (identical to v1) |
| selection_algorithm / seeds | `sha256_rank_v1` / selection 20260815 / ordering 20260815 (identical to v1) |
| plan_sha256 | `sha256:eb40964c3d1221f1c05a2c73ceab13513e8fb47ad3d1370987975bf7130edd65` (identical to v1 -- same 50 tasks, same order) |
| plan_lock_commit_sha | → tag `corpus-plan-v2` (this commit) |
| status | `LOCKED` |

The 50 rows are exactly those in `docs/corpus-plan.md` (corpus-plan-v1); they are not duplicated here.
The specs' informational `runtime:` line still reads v1 but is OVERRIDDEN by this plan's
`observation_runtime` stamp (the authoritative binding); the spec BYTES and their `task_spec_sha256`
are unchanged, which is why `plan_sha256` is identical.

## Why v2 (recorded)

Four-pilot gate on v0.3 was INDETERMINATE (thin reads ~20, MissedContextTokenShare 0.296); the pilot
showed agents navigate via grep/find/compound shell that v0.3 marked `unknown`. v0.4 (representation-
typed shell + compound splitting) recovers it: re-analyzing the exact pilot-3/4 commands under v0.4
(after an adversarial audit fixed 4 recognizer defects) gives 32 reads and MissedContextTokenShare
**0.000** -- decisively clearing the preregistered A gate. See `docs/pilot-decision-gate.md`.

## v1 stays immutable

`obs-runtime-3a-v1`→484f4b3 and `corpus-plan-v1`→3d0a0d6 remain as the immutable v0.3 artifacts.

## Not permitted

Redrawing/ reordering the 50 tasks; editing a spec; running against any commit other than
`obs-runtime-3a-v2`.
