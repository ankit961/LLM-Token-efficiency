# Task spec — run-40  (IMMUTABLE)

task_id: django__django-11477
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: e28671187903e6aca2428374fdd504fca3032aee
fix_shape_evidence: n_src_files=1, src_lines=2   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_re_path_with_optional_parameter (urlpatterns.tests.SimplifiedURLTests)
  - test_two_variable_at_start_of_path_pattern (urlpatterns.tests.SimplifiedURLTests)
  - test_translate_url_utility (i18n.patterns.tests.URLTranslationTests)
PASS_TO_PASS_count: 151
PASS_TO_PASS_sha256: sha256:41638a7ad7d2e4bc6de897e965476b2eac1d0e3186e1fba0d4cd4b91c121bbfe

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
translate_url() creates an incorrect URL when optional named groups are missing in the URL pattern
Description
	
There is a problem when translating urls with absent 'optional' arguments
(it's seen in test case of the patch)

