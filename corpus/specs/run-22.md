# Task spec — run-22  (IMMUTABLE)

task_id: django__django-11119
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: d4df5e1b0b1c643fe0fc521add0236764ec8e92a
fix_shape_evidence: n_src_files=1, src_lines=2   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_autoescape_off (template_tests.test_engine.RenderToStringTest)
PASS_TO_PASS_count: 7
PASS_TO_PASS_sha256: sha256:16957940114c06976753f75d6a138c33886aca3db7aebb584c70c8835dfce70f

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Engine.render_to_string() should honor the autoescape attribute
Description
	
In Engine.render_to_string, a Context is created without specifying the engine autoescape attribute. So if you create en engine with autoescape=False and then call its render_to_string() method, the result will always be autoescaped. It was probably overlooked in [19a5f6da329d58653bcda85].

