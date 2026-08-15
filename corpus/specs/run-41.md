# Task spec — run-41  (IMMUTABLE)

task_id: django__django-16527
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: bd366ca2aeffa869b7dbc0b0aa01caea75e6dc31
fix_shape_evidence: n_src_files=1, src_lines=2   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_submit_row_save_as_new_add_permission_required (admin_views.test_templatetags.AdminTemplateTagsTest.test_submit_row_save_as_new_add_permission_required)
PASS_TO_PASS_count: 6
PASS_TO_PASS_sha256: sha256:2d5eaf4aac1440f3a6ddc2909e7cc9fb0081bc42b5e75f5445d7594d90ecc6d0

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
"show_save_as_new" in admin can add without this permission
Description
	 
		(last modified by Mariusz Felisiak)
	 
At "django/contrib/admin/templatetags/admin_modify.py" file, line 102, I think you must put one more verification for this tag: "and has_add_permission", because "save_as_new" is a add modification.
I rewrite this for my project:
			"show_save_as_new": not is_popup
			and has_add_permission # This line that I put!!!
			and has_change_permission
			and change
			and save_as,

