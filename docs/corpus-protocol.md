# Corpus collection protocol — PREREGISTRATION (label-validity corpus)

**Status: preregistered draft, to be locked before ANY collection begins.** The point of writing this
before we collect is to remove researcher degrees of freedom — especially task selection — so the
eventual exploration/precondition numbers are defensible. Nothing here touches the frozen evidence
contract (`hook_schema 0.3.0`) or the classifier; it governs *how we gather and admit journals*.

This corpus feeds **Slice 3A label validity** (are the observed labels stable and trustworthy) and,
only if labels look stable, later **Slice 3B** cross-channel measurement. It is **not** a
token-savings dataset — no bypass/CED/BundleNetSaving number comes from it.

## Unit of analysis

- **unit** = one *closed* task / lineage epoch (`stream_key = session:agent:epoch`).
- A journal may hold several units; each unit's closure is asserted in the run's closure manifest.

## Per-unit stamps (recorded in the manifest / run log, never inferred later)

| field | rule |
|---|---|
| `client` | exact Claude Code version (e.g. `2.1.229`). **One journal DB = one client version.** If a session spans an upgrade, split the DB, or carry `client_version` per stream in the manifest. |
| `model` | exact model id used for the task. |
| `task_end` | how the unit closed: `controlled_run_completed` or `/clear` (auto-closed prior epoch). |
| `closed_at_seq` | the last observed seq for that stream; reads beyond it are right-censored, not settled. |

## Minimum-evidence admission gate (a unit is ADMITTED only if all hold)

- `pre_capture_rate` ≈ 1.0 (no dropped `PreToolUse` deliveries).
- `pending_tools` == 0 at close (every `PreToolUse` matched a `PostToolUse`).
- No `capture_errors` that affected a tool lifecycle (a stray error outside the Pre→Post→Batch path is
  acceptable and logged; one that dropped or corrupted a tool event disqualifies the unit).
- `canonical_validity == true` in the report (no primary EDIT_PRECONDITION fell back to `seq`).

Units failing the gate are **excluded and counted** (report the exclusion, never silently drop).

## Task selection — the anti-bias rules (the important part)

- **Do NOT choose tasks after seeing their exploration rate.** Task identity is fixed *before* the run.
- Collect a **predefined mixture** of coding behaviors, targeting rough balance across:
  1. navigation / debugging (read-heavy, few edits)
  2. local bug fix (small, edit-precondition-heavy)
  3. multi-file feature (reads + several edits across files)
  4. refactor (many edits, re-reads / verification)
  5. tests / config work (config-path reads, test reruns)
- **Retain failures.** A task where the agent didn't finish is part of the corpus, not a reject — its
  labels are as valid as a success's. Dropping failures is itself selection bias.

## Preservation & provenance

- The raw journal DB is **immutable** once a unit closes; record its `sha256`.
- Every quoted number carries: `report_schema`, `hook_schema 0.3.0`, classifier commit SHA, journal
  hash, client/model stamps, closure manifest, token estimator id, primary window.
- Reporting: **W=16 primary**, W∈{8,32,∞} sensitivity only — never tuned. The shared artifact is
  aggregate-only with pseudonymized stream ids.

## Immutable-benchmark rule

Once collection begins, a `hook_schema` change ⇒ a **new** corpus artifact, never a mutation of an
existing one. Mixing schema versions in one measurement store is prohibited.

## What this corpus does NOT license

- No ExplorationBypassRate (needs SemanticFS events — Slice 3B, after a live-validated join).
- No population token-savings claim.
- No predicted-class / confusion-matrix claim (that comes after the observed ground truth is frozen).
