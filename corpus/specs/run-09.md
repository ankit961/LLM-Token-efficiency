# Task spec — run-09  (IMMUTABLE)

task_id: django__django-10880
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 838e432e3e5519c5383d12018e6c78f8ec7833c1
fix_shape_evidence: n_src_files=1, src_lines=2   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_count_distinct_expression (aggregation.tests.AggregateTestCase)
PASS_TO_PASS_count: 55
PASS_TO_PASS_sha256: sha256:afbd4a18ed4dda5367cbd8611d697481f59377874643ff0347bb5c32fbf78d80

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Query syntax error with condition and distinct combination
Description
	
A Count annotation containing both a Case condition and a distinct=True param produces a query error on Django 2.2 (whatever the db backend). A space is missing at least (... COUNT(DISTINCTCASE WHEN ...).

