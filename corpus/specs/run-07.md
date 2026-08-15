# Task spec — run-07  (IMMUTABLE)

task_id: django__django-12406
category: fs5_multi_ge2f
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: 335c9c94acf263901fb023404408880245b0c4b4
fix_shape_evidence: n_src_files=2, src_lines=17   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_non_blank_foreign_key_with_radio (model_forms.tests.ModelFormBaseTest)
  - test_choices_radio_blank (model_forms.test_modelchoicefield.ModelChoiceFieldTests)
  - test_clean_model_instance (model_forms.test_modelchoicefield.ModelChoiceFieldTests)
PASS_TO_PASS_count: 165
PASS_TO_PASS_sha256: sha256:1b2c52385f689a09bb2bf5af9f25e0b28ac2d11dcba1424e02462cf16e523c40

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
ModelForm RadioSelect widget for foreign keys should not present a blank option if blank=False on the model
Description
	
Unlike the select widget, where a blank option is idiomatic even for required fields, radioselect has an inherent unfilled state that makes the "-------" option look suspiciously like a valid choice.
class TestRun(models.Model):
	data_file = models.ForeignKey(BatchData, on_delete=models.SET_NULL, null=True, blank=False)
class TestRunForm(ModelForm):
	class Meta:
		model = TestRun
		fields = ['data_file']
		widgets = {'data_file': RadioSelect()}
renders {{test_run_form.data_file}} as
<ul id="id_data_file">
 <li><label for="id_data_file_0">
	<input checked="checked" id="id_data_file_0" name="data_file" type="radio" value=""> ---------
 </label></li>
 <li><label for="id_data_file_1">
	<input id="id_data_file_1" name="data_file" type="radio" value="1"> First Data File
 </label></li>
</ul>
Instead, there should be no checked option for RadioSelect's <input> tags when rendering a new form from a model if blank is not a valid selection.

