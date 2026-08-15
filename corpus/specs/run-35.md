# Task spec — run-35  (IMMUTABLE)

task_id: django__django-11433
category: fs3_medium_1f_7_15
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 21b1d239125f1228e579b1ce8d94d4d5feadd2a6
fix_shape_evidence: n_src_files=1, src_lines=7   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_default_not_populated_on_non_empty_value_in_cleaned_data (model_forms.tests.ModelFormBaseTest)
PASS_TO_PASS_count: 142
PASS_TO_PASS_sha256: sha256:627f01462cf758b539e7cbfce1c326510c0f1e0c1b60eaac0c4e2addff0dc571

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Allow `cleaned_data` to overwrite fields' default values.
Description
	
See comments here: ​https://github.com/django/django/pull/7068/files#r289432409
Currently, when submitting a form, if 'some_field' isn't in the data payload (e.g. it wasn't included in the form, perhaps because its value is derived from another field), and 'some_field' has a default value on the model, it cannot be overwritten with 'self.cleaned_data'.
This does not really follow the paradigm of modifying data in 'cleaned_data'. It requires the user to copy and overwrite the raw data submitted with the form.

