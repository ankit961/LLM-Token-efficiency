# Task spec — run-03  (IMMUTABLE)

task_id: django__django-10999
category: fs3_medium_1f_7_15
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 36300ef336e3f130a0dadc1143163ff3d23dc843
fix_shape_evidence: n_src_files=1, src_lines=7   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_negative (utils_tests.test_dateparse.DurationParseTests)
  - test_parse_postgresql_format (utils_tests.test_dateparse.DurationParseTests)
PASS_TO_PASS_count: 10
PASS_TO_PASS_sha256: sha256:c1cccf43d59ae22fa7f5737171fb0b130fe30898e07c4268c70e5e34c87f2a84

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Fix parse_duration() for some negative durations
Description
	
The ​https://docs.djangoproject.com/en/2.1/_modules/django/utils/dateparse/ defines:
standard_duration_re = re.compile(
	r'^'
	r'(?:(?P<days>-?\d+) (days?, )?)?'
	r'((?:(?P<hours>-?\d+):)(?=\d+:\d+))?'
	r'(?:(?P<minutes>-?\d+):)?'
	r'(?P<seconds>-?\d+)'
	r'(?:\.(?P<microseconds>\d{1,6})\d{0,6})?'
	r'$'
)
that doesn't match to negative durations, because of the <hours> definition final (lookahead) part does not have '-?' in it. The following will work:
	r'((?:(?P<hours>-?\d+):)(?=-?\d+:-?\d+))?'
(Thanks to Konstantin Senichev for finding the fix.)

