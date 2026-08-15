# Task spec — run-01  (IMMUTABLE)

task_id: django__django-10973
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: ddb293685235fd09e932805771ae97f72e817181
fix_shape_evidence: n_src_files=1, src_lines=37   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_accent (dbshell.test_postgresql.PostgreSqlDbshellCommandTestCase)
  - test_basic (dbshell.test_postgresql.PostgreSqlDbshellCommandTestCase)
  - test_column (dbshell.test_postgresql.PostgreSqlDbshellCommandTestCase)
  - test_nopass (dbshell.test_postgresql.PostgreSqlDbshellCommandTestCase)
  - SIGINT is ignored in Python and passed to psql to abort quries.
PASS_TO_PASS_count: 0
PASS_TO_PASS_sha256: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Use subprocess.run and PGPASSWORD for client in postgres backend
Description
	
​subprocess.run was added in python 3.5 (which is the minimum version since Django 2.1). This function allows you to pass a custom environment for the subprocess.
Using this in django.db.backends.postgres.client to set PGPASSWORD simplifies the code and makes it more reliable.

