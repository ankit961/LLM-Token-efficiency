# Task spec — run-26  (IMMUTABLE)

task_id: django__django-11734
category: fs5_multi_ge2f
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 999891bd80b3d02dd916731a7a239e1036174885
fix_shape_evidence: n_src_files=3, src_lines=10   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_subquery_exclude_outerref (queries.tests.ExcludeTests)
PASS_TO_PASS_count: 275
PASS_TO_PASS_sha256: sha256:7979048bbe011d2746b2901461191c12662d4fcfe1517f057df70243d3728e9f

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
OuterRef in exclude() or ~Q() uses wrong model.
Description
	
The following test (added to tests/queries/test_qs_combinators) fails when trying to exclude results using OuterRef()
def test_exists_exclude(self):
	# filter()
	qs = Number.objects.annotate(
		foo=Exists(
			Item.objects.filter(tags__category_id=OuterRef('pk'))
		)
	).filter(foo=True)
	print(qs) # works
	# exclude()
	qs = Number.objects.annotate(
		foo =Exists(
			Item.objects.exclude(tags__category_id=OuterRef('pk'))
		)
	).filter(foo=True)
	print(qs) # crashes
	# filter(~Q())
	qs = Number.objects.annotate(
		foo =Exists(
			Item.objects.filter(~Q(tags__category_id=OuterRef('pk')))
		)
	).filter(foo=True)
	print(qs) # crashes
It results in the following error
ValueError: This queryset contains a reference to an outer query and may only be used in a subquery

