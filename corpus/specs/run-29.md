# Task spec — run-29  (IMMUTABLE)

task_id: django__django-11292
category: fs3_medium_1f_7_15
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: eb16c7260e573ec513d84cb586d96bdf508f3173
fix_shape_evidence: n_src_files=1, src_lines=11   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_skip_checks (user_commands.tests.CommandRunTests)
PASS_TO_PASS_count: 31
PASS_TO_PASS_sha256: sha256:3c653bf902d49f7e8a68d46efe4cb59892f2afa73f188821acd79c25935a9c1f

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Add --skip-checks option to management commands.
Description
	
Management commands already have skip_checks stealth option. I propose exposing this option on the command line. This would allow users to skip checks when running a command from the command line. Sometimes in a development environment, it is nice to move ahead with a task at hand rather than getting side tracked fixing a system check.

