# Task spec — run-44  (IMMUTABLE)

task_id: django__django-12713
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: 5b884d45ac5b76234eca614d90c83b347294c332
fix_shape_evidence: n_src_files=1, src_lines=30   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_formfield_overrides_m2m_filter_widget (admin_widgets.tests.AdminFormfieldForDBFieldTests)
PASS_TO_PASS_count: 58
PASS_TO_PASS_sha256: sha256:949af4f611805d36ca196287016435e4ecaf130ac278c125f837bad129b3cb26

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Allow overridding widget in formfield_for_manytomany().
Description
	 
		(last modified by Mariusz Felisiak)
	 
It does not work when I set widget param to function formfield_for_manytomany().
This is different from the formfield_for_foreignkey() function.

