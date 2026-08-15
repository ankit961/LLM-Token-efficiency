# Task spec — run-02  (IMMUTABLE)

task_id: django__django-11095
category: fs2_small_1f_4_6
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 7d49ad76562e8c0597a0eb66046ab423b12888d8
fix_shape_evidence: n_src_files=1, src_lines=6   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_get_inline_instances_override_get_inlines (generic_inline_admin.tests.GenericInlineModelAdminTest)
PASS_TO_PASS_count: 19
PASS_TO_PASS_sha256: sha256:c4cab3e3b1d1b8c603fafb7dc2c98c18ca1c27530d6b9379d934e9518c19fa83

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
add ModelAdmin.get_inlines() hook to allow set inlines based on the request or model instance.
Description
	
add ModelAdmin.get_inlines() hook to allow set inlines based on the request or model instance.
Currently, We can override the method get_inline_instances to do such a thing, but a for loop should be copied to my code. So I wished add a hook get_inlines(request, obj=None)

