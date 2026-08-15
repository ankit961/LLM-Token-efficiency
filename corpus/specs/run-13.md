# Task spec — run-13  (IMMUTABLE)

task_id: django__django-15382
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: 770d3e6a4ce8e0a91a9e27156036c1985e74d4a3
fix_shape_evidence: n_src_files=1, src_lines=19   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_negated_empty_exists (expressions.tests.ExistsTests)
PASS_TO_PASS_count: 161
PASS_TO_PASS_sha256: sha256:4569a4da966a93e77da831e0defabc62b5a2719bf76b3343cfcb650589c8e75e

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
filter on exists-subquery with empty queryset removes whole WHERE block
Description
	 
		(last modified by Tobias Bengfort)
	 
>>> qs = MyModel.objects.filter(~models.Exists(MyModel.objects.none()), name='test')
>>> qs
<QuerySet []>
>>> print(qs.query)
EmptyResultSet
With django-debug-toolbar I can still see the query, but there WHERE block is missing completely.
This seems to be very similar to #33018.

