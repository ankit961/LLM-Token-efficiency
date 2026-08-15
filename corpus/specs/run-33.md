# Task spec — run-33  (IMMUTABLE)

task_id: django__django-12050
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: b93a0e34d9b9b99d41103782b7e7aeabf47517e3
fix_shape_evidence: n_src_files=1, src_lines=19   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_iterable_lookup_value (queries.test_query.TestQuery)
PASS_TO_PASS_count: 10
PASS_TO_PASS_sha256: sha256:bd520dd90d26299f2195174e4fda6f3b0dc783155e172ca0d6900ab43cabff10

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Query.resolve_lookup_value coerces value of type list to tuple
Description
	
Changes introduced in #30687 cause an input value list to be coerced to tuple breaking exact value queries. This affects ORM field types that are dependent on matching input types such as PickledField.
The expected iterable return type should match input iterable type.

