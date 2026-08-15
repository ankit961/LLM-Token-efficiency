# Task spec — run-24  (IMMUTABLE)

task_id: django__django-11790
category: fs2_small_1f_4_6
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: b1d6b35e146aea83b171c1b921178bbaae2795ed
fix_shape_evidence: n_src_files=1, src_lines=4   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_username_field_max_length_defaults_to_254 (auth_tests.test_forms.AuthenticationFormTest)
  - test_username_field_max_length_matches_user_model (auth_tests.test_forms.AuthenticationFormTest)
PASS_TO_PASS_count: 77
PASS_TO_PASS_sha256: sha256:48b3ab1b87d1e0b115205d0b13d4106cd25ba90d303004e62a91cf32e84d5786

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
AuthenticationForm's username field doesn't set maxlength HTML attribute.
Description
	
AuthenticationForm's username field doesn't render with maxlength HTML attribute anymore.
Regression introduced in #27515 and 5ceaf14686ce626404afb6a5fbd3d8286410bf13.
​https://groups.google.com/forum/?utm_source=digest&utm_medium=email#!topic/django-developers/qnfSqro0DlA
​https://forum.djangoproject.com/t/possible-authenticationform-max-length-regression-in-django-2-1/241

