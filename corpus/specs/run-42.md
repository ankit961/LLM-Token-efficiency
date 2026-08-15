# Task spec — run-42  (IMMUTABLE)

task_id: django__django-12308
category: fs2_small_1f_4_6
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 2e0f04507b17362239ba49830d26fec504d46978
fix_shape_evidence: n_src_files=1, src_lines=5   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_json_display_for_field (admin_utils.tests.UtilsTests)
  - test_label_for_field (admin_utils.tests.UtilsTests)
PASS_TO_PASS_count: 20
PASS_TO_PASS_sha256: sha256:815c1e8b52dd5fa8f5296dcc4365f97ecff9fffd0b201f7109d20b955c905529

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
JSONField are not properly displayed in admin when they are readonly.
Description
	
JSONField values are displayed as dict when readonly in the admin.
For example, {"foo": "bar"} would be displayed as {'foo': 'bar'}, which is not valid JSON.
I believe the fix would be to add a special case in django.contrib.admin.utils.display_for_field to call the prepare_value of the JSONField (not calling json.dumps directly to take care of the InvalidJSONInput case).

