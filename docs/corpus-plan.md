# Corpus plan v1 — the frozen task list

**Status: TEMPLATE — TO BE FILLED AND LOCKED BEFORE TASK 1.** This file is the preregistered,
ordered task list for the label-validity corpus (see `docs/corpus-protocol.md`). Locking = committing
this file with all 50 rows filled, on the observation-runtime commit, **before any run executes**.
Once locked, the list is not edited by outcome; failures remain in place.

- **N = 50 runs = 10 tasks × 5 categories.**
- Each task's `task_spec_sha256` is the SHA-256 of its immutable task-spec file (objective
  success/`task_outcome` condition included in the spec).
- `run_order` is the fixed execution order (1..50), frozen before task 1.
- Do NOT choose or reorder tasks after seeing any labels. Retain failures.

## Freeze stamps (fill at lock time)

| field | value |
|---|---|
| observation_runtime_sha | `<git SHA after PR#2+PR#3 merge to main>` |
| classifier_blob_sha | `<sha256 of classify.py at that SHA>` |
| hook_schema | `0.3.0` |
| report_schema | `label-report-0.2.0` |
| client_version | `<exact Claude Code version>` |
| model_id | `<exact model id>` |
| settings_sha256 | `<sha256 of the cr-hook settings.json used>` |
| locked_by | `<name>` |
| locked_at | `<UTC timestamp>` |
| plan_sha256 | `<sha256 of this file once filled>` |

## Task table (fill all 50 rows before running)

| run_order | task_id | task_category | task_spec_sha256 | repo_id | base_commit_sha |
|---:|---|---|---|---|---|
| 1 | `TBD` | navigation_debugging | `TBD` | `TBD` | `TBD` |
| 2 | `TBD` | local_bug_fix | `TBD` | `TBD` | `TBD` |
| 3 | `TBD` | multi_file_feature | `TBD` | `TBD` | `TBD` |
| 4 | `TBD` | refactor | `TBD` | `TBD` | `TBD` |
| 5 | `TBD` | tests_config | `TBD` | `TBD` | `TBD` |
| … | … | … | … | … | … |
| 50 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` |

**Category counts must be exactly:** navigation_debugging=10, local_bug_fix=10, multi_file_feature=10,
refactor=10, tests_config=10.

## Not permitted

- Editing a row after any run's labels are observed.
- Substituting a task because its exploration rate "looks off".
- Recycling a `PILOT / NOT IN CORPUS` run into this table.
- Tuning W to a task's stability. Report whatever W-sensitivity the frozen corpus shows.
