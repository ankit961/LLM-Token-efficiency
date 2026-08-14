# Task spec — run-NN (TEMPLATE — copy to corpus/specs/run-NN.md and fill)

**Immutable once authored.** A spec is finalized, its bytes are hashed (`sha256`), and that hash goes
into the corresponding row of `docs/corpus-plan.md`. Editing a spec after its hash is recorded breaks
the preregistration. The success criterion is OBJECTIVE and machine-checkable — the agent declaring
"done" is NOT the outcome.

```yaml
task_id:            # stable unique id, e.g. corpus-v1-r07
category:           # one of: navigation_debugging | local_bug_fix | multi_file_feature | refactor | tests_config
                    # MUST match the category preassigned to this run_order in docs/corpus-plan.md

repo_id:            # the target repository identity
base_commit_sha:    # the exact commit the run starts from (clean isolated worktree at this SHA)

task_prompt: |      # the verbatim instruction given to the agent (the ONLY thing it sees)
  ...

allowed_scope:      # files/dirs the task may touch (or "unrestricted"); used only for description,
                    # NOT enforced by the observation layer

objective_success_criterion: |   # a predetermined, objective condition — NOT the agent's self-report
  ...
verification_command:            # exact command whose exit code / output decides task_outcome
                                 # e.g. `pytest tests/test_foo.py -q`  (exit 0 == success)

turn_or_walltime_budget:         # fixed budget (e.g. 40 turns, or 15 min wall) — frozen before the run
permission_mode:                 # e.g. bypassPermissions (headless controlled run)
headless_or_interactive:         # headless | interactive

special_setup:                   # any fixed setup steps (seed data, env), or "none"
```

## Run-time stamps (recorded by the runner, NOT part of the hashed spec)

`run_id`, `client_version`, `model_id`, `runtime_commit_sha` (= `484f4b35…`, via `obs-runtime-3a-v1`),
`hook_schema` (`0.3.0`), `report_schema` (`label-report-0.2.0`), `settings_sha256`, `start_ts`,
`end_ts`, `task_outcome` (from `verification_command`), `closure_reason`
(`controlled_run_completed | timeout | aborted`), `closed_at_seq`, `journal_sha256`, `manifest_sha256`.

## Admission (whole-run, from the label-report of the run's journal)

A run is canonical-admissible iff `admission.canonical_admissible` is true (see
`docs/corpus-protocol.md`). `token_share_eligible` and `bash_coverage_clean` are separate flags;
runs that fail coverage are RETAINED and counted, never deleted.
