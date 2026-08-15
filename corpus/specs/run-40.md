# Task spec — run-40  (IMMUTABLE)

task_id: django__django-11179
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 19fc6376ce67d01ca37a91ef2f55ef769f50513a
fix_shape_evidence: n_src_files=1, src_lines=1   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_fast_delete_instance_set_pk_none (delete.tests.FastDeleteTests)
PASS_TO_PASS_count: 40
PASS_TO_PASS_sha256: sha256:307bbceaee3028bc22e73aea080648b07bbe4320d87c5ac3ec179eb50d837bff

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
delete() on instances of models without any dependencies doesn't clear PKs.
Description
	
Deleting any model with no dependencies not updates the PK on the model. It should be set to None after .delete() call.
See Django.db.models.deletion:276-281. Should update the model line 280.

