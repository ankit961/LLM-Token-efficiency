# Corpus collection protocol — PREREGISTRATION (label-validity corpus)

**Status: preregistered, to be LOCKED before ANY collection begins.** Writing this before we collect
removes researcher degrees of freedom — especially task selection and admission — so the eventual
exploration/precondition numbers are defensible. Nothing here touches the frozen evidence contract
(`hook_schema 0.3.0`) or the classifier; it governs *how we gather and admit journals*.

This corpus feeds **Slice 3A label validity** (are the observed labels stable and trustworthy) and,
only after the predefined corpus is finished, **Slice 3B** cross-channel measurement. It is **not** a
token-savings dataset — no bypass/CED/BundleNetSaving number comes from it.

## 1. Unit of analysis — the TASK RUN

- **unit = one controlled task run = one HookJournal DB.** Not a stream.
- Hierarchy: `task_run → stream / agent / epoch → events`. A task can contain a main-agent stream,
  subagent streams, and `/clear` epochs; those are **nested observations**, not independent units.
- Rationale: `capture_stats` are journal-global and `capture_errors` carry no stream key, so you
  cannot rigorously attribute a missing hook or capture error to a specific stream. One DB per task
  makes the client/model/environment stamp unambiguous and prevents pseudo-replication (five
  subagents are one task, not five).
- **task run = the sampling unit.** Report both **micro** (read-weighted) and **macro** (task-weighted)
  statistics; later use **task-cluster bootstrap** confidence intervals — never treat hundreds of
  reads from one task as independent observations.

## 2. Admission gate — EXACT and mechanical (whole-run)

A run is **canonical-admissible** (for event-label analysis) iff ALL of `admission.checks` are true
in its report — these are exact booleans, not interpretive thresholds:

| check | rule |
|---|---|
| `pre_equals_batch` | `pre_tool_calls_seen == batch_tool_calls_resolved` (no dropped PreToolUse) |
| `zero_errors` | `errors == 0` |
| `zero_pending` | `pending_tools == 0` (every PreToolUse matched a PostToolUse) |
| `hook_schema_expected` | `hook_schema == 0.3.0` |
| `canonical_report` | canonical window set (primary=16, {8,16,32,∞}) |
| `canonical_validity` | `missing_step_reads == missing_step_mutations == seq_fallback_preconditions == 0` |
| `provenance_complete` | `client_version` and `runtime_commit_sha` both stamped |

Two SEPARATE gates layer on top:

- **`bash_coverage_clean`** (`unknown_bash_calls == 0`) marks the strict label-validity coverage
  subset. Runs that fail it are **retained and counted as coverage-failed, stratified by task
  category** — never deleted.
- **`token_share_eligible`** (100% fully-measured *text* reads) gates the **token-share headline
  only**. A run with an image/PDF read stays valid for event-label analysis but is excluded from
  canonical token-share.

## 3. Per-run manifest (recorded before/at the run, never inferred later)

Required, at minimum: `run_id`, `task_id`, `task_category`, `task_spec_sha256`, `repo_id`,
`base_commit_sha`, `client_version`, `model_id`, `runtime_commit_sha`, `hook_schema`, `report_schema`,
`settings_sha256`, `permission_mode`, `headless_or_interactive`, `start_ts`, `end_ts`,
`turn_or_walltime_budget`, `task_outcome`, `closure_reason`, `closed_at_seq`, `journal_sha256`,
`manifest_sha256`.

- `closure_reason` ∈ `{controlled_run_completed, superseded_clear_epoch, timeout, aborted}` (enum —
  free text is rejected by the reporter, since it could smuggle identifiers into the artifact).
- `task_outcome` is decided by a **predetermined objective condition/test** where possible, NOT by the
  agent declaring success.
- Every run starts from a **clean isolated worktree at `base_commit_sha`**, so a run is
  reconstructible, not merely traceable.

## 4. Frozen task plan — v1

- **N = 50 task runs: exactly 10 preselected tasks in each of 5 categories** —
  navigation/debugging, local bug fix, multi-file feature, refactor, tests/config.
- The **exact ordered task list** (`task_id` + `task_spec_sha256` + run order) is committed in
  `docs/corpus-plan.md` and LOCKED **before executing task 1**. If 50 is too expensive, pick another N
  now — the property that matters is that N and the ordered list are frozen before any labels are seen.
- **Do NOT choose tasks after seeing their exploration rate.** **Retain failures in place.**

## 5. Preservation & immutability

- The raw journal DB is **immutable** once a run closes; record its `sha256`.
- Once collection begins, a `hook_schema` change ⇒ a **new** corpus artifact, never a mutation of an
  existing one. Mixing schema versions in one measurement store is prohibited.

## 6. Ordering — do NOT collect the confirmatory corpus yet

Collection must wait for the final combined-tree code snapshot:

```
Slice 3A.2 (this) → WAIT for adapters.py → main
  → rebase PR#2 → combined-tree CI → merge
  → rebase/retarget PR#3 → main → full CI → merge
  → record/tag the exact observation-runtime SHA
  → LOCK corpus protocol + LOCK corpus plan
  → collect 50 closed task-run journals → freeze raw corpus + hashes
  → 3A corpus validity analysis
  → 3B live-validate SemanticFS ↔ HookJournal join → bypass / CED / BundleNetSaving
```

- **Window instability does NOT decide which tasks survive** and must NOT trigger a tuned W. Once the
  predefined corpus is finished, freeze it and carry whatever W-sensitivity you observe into 3B.
- **Pilot harness runs while waiting are permitted but permanently marked `PILOT / NOT IN CORPUS`** —
  never recycled into the confirmatory 50 after their labels are seen.

## 7. What this corpus does NOT license

- No ExplorationBypassRate (needs SemanticFS events — Slice 3B, after a live-validated join).
- No population token-savings claim.
- No predicted-class / confusion-matrix claim (comes after the observed ground truth is frozen).
- The censoring **bounds** and the complete-case figure are identification/descriptive statistics —
  **not** sampling confidence intervals.
