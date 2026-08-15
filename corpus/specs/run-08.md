# Task spec — run-08  (IMMUTABLE)

task_id: django__django-11099
category: fs2_small_1f_4_6
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: d26b2424437dabeeca94d7900b37d2df4410da0c
fix_shape_evidence: n_src_files=1, src_lines=4   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_ascii_validator (auth_tests.test_validators.UsernameValidatorsTests)
  - test_unicode_validator (auth_tests.test_validators.UsernameValidatorsTests)
  - test_help_text (auth_tests.test_validators.UserAttributeSimilarityValidatorTest)
PASS_TO_PASS_count: 19
PASS_TO_PASS_sha256: sha256:4da7d26dcd434a435f19fbc1df2e3e12e519f29d9938815692c7634696d27fb5

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
UsernameValidator allows trailing newline in usernames
Description
	
ASCIIUsernameValidator and UnicodeUsernameValidator use the regex 
r'^[\w.@+-]+$'
The intent is to only allow alphanumeric characters as well as ., @, +, and -. However, a little known quirk of Python regexes is that $ will also match a trailing newline. Therefore, the user name validators will accept usernames which end with a newline. You can avoid this behavior by instead using \A and \Z to terminate regexes. For example, the validator regex could be changed to
r'\A[\w.@+-]+\Z'
in order to reject usernames that end with a newline.
I am not sure how to officially post a patch, but the required change is trivial - using the regex above in the two validators in contrib.auth.validators.

