# Corpus plan v2.1 — LOCKED (v0.4.1 observation runtime)

**Status: LOCKED.** The task SAMPLE is byte-identical to `corpus-plan-v1`/`v2` — same universe, seeds,
50 `corpus/specs/run-NN.md`, order, and `plan_sha256`. The ONLY change is the observation runtime:
independent review found evidence-boundary defects in v0.4.0 (conditional/background effects, cd cwd,
composite token attribution, hidden partial recognition, search-as-exploration), all repaired in
**`hook_schema 0.4.1`**. Selection is NEVER reopened.

    Experiment_v2.1 = (Runtime Snapshot v2.1, Preregistered Plan v1-sample)

## Freeze stamps

| field | value |
|---|---|
| observation_runtime_sha | `59d1904204e2afbdc8a05941a27d7ef690f8827e` |
| observation_runtime_tag | `obs-runtime-3a-v2.1` |
| hook_schema | `0.4.1` (execution-certainty, virtual-cwd, composite attribution, coverage ledger) |
| report_schema | `label-report-0.2.1` |
| selection_universe_sha256 | `sha256:adf617753df1fa2413786a1ed7467f24f858fb32787635a230b7c7dc0ee14f30` (identical to v1/v2) |
| selection/ordering seeds | `20260815` / `20260815` (identical) |
| plan_sha256 | `sha256:eb40964c3d1221f1c05a2c73ceab13513e8fb47ad3d1370987975bf7130edd65` (identical -- same 50 tasks, same order) |
| plan_lock_commit_sha | → tag `corpus-plan-v2.1` (this commit) |
| status | `LOCKED` |

The 50 rows are exactly those in `docs/corpus-plan.md`; not duplicated. The spec bytes and their
`task_spec_sha256` are unchanged (so `plan_sha256` is identical); the specs' informational `runtime:`
line is OVERRIDDEN by this plan's `observation_runtime` stamp.

## Provenance chain (all preserved, immutable)

    v1   obs-runtime-3a-v1   → 484f4b3   hook_schema 0.3.0   corpus-plan-v1   → 3d0a0d6
    v2   obs-runtime-3a-v2   → 43ecd95   hook_schema 0.4.0   corpus-plan-v2   → 8c34641   (superseded RC)
    v2.1 obs-runtime-3a-v2.1 → 59d1904   hook_schema 0.4.1   corpus-plan-v2.1 → (this)   <-- USE THIS

v2 is kept as audit history (a pre-collection release candidate); v2.1 is the runtime to collect on.

## Why v2.1 (recorded)

v0.4.0 recovered shell materialization but its parser let uncertain/composite/partial shell flow into
evidence-grade role and token statistics. 0.4.1 makes the boundary honest: phantom effects 0,
composite-falsely-attributed 0, search-as-EXPLORATION 0, partial recognition preserved (verified by
replaying the frozen pilot taps under 0.4.1). See `docs/pilot-decision-gate.md`.

## Not permitted

Redrawing/reordering the 50 tasks; editing a spec; running against any commit other than
`obs-runtime-3a-v2.1`.
