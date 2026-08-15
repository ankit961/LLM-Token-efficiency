# Task spec — run-45  (IMMUTABLE)

task_id: django__django-13128
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 2d67222472f80f251607ae1b720527afceba06ad
fix_shape_evidence: n_src_files=1, src_lines=43   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_date_case_subtraction (expressions.tests.FTimeDeltaTests)
  - test_date_subquery_subtraction (expressions.tests.FTimeDeltaTests)
  - test_date_subtraction (expressions.tests.FTimeDeltaTests)
  - test_datetime_subquery_subtraction (expressions.tests.FTimeDeltaTests)
  - test_datetime_subtraction_microseconds (expressions.tests.FTimeDeltaTests)
  - test_time_subquery_subtraction (expressions.tests.FTimeDeltaTests)
  - test_time_subtraction (expressions.tests.FTimeDeltaTests)
PASS_TO_PASS_count: 134
PASS_TO_PASS_sha256: sha256:fffcad2f2dfc75e9c6052bdfb6faa5fe6924ceede5ec11b22bd073c3d2992fee

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
make temporal subtraction work without ExpressionWrapper
Description
	
class Experiment(models.Model):
	start = models.DateTimeField()
	end = models.DateTimeField()
Experiment.objects.annotate(
	delta=F('end') - F('start') + Value(datetime.timedelta(), output_field=DurationField())
)
This gives:
django.core.exceptions.FieldError: Expression contains mixed types: DateTimeField, DurationField. You must set output_field.

