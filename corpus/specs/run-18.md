# Task spec — run-18  (IMMUTABLE)

task_id: django__django-11400
category: fs5_multi_ge2f
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 1f8382d34d54061eddc41df6994e20ee38c60907
fix_shape_evidence: n_src_files=3, src_lines=26   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_get_choices_default_ordering (model_fields.tests.GetChoicesOrderingTests)
  - test_get_choices_reverse_related_field_default_ordering (model_fields.tests.GetChoicesOrderingTests)
  - RelatedFieldListFilter ordering respects Model.ordering.
  - test_relatedfieldlistfilter_reverse_relationships_default_ordering (admin_filters.tests.ListFiltersTests)
  - RelatedOnlyFieldListFilter ordering respects Meta.ordering.
  - RelatedOnlyFieldListFilter ordering respects ModelAdmin.ordering.
PASS_TO_PASS_count: 58
PASS_TO_PASS_sha256: sha256:a3bf533878fe0dc92ac401a2633cba61350de5e63a3fe2d08d6d404a8df2ff88

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Ordering problem in admin.RelatedFieldListFilter and admin.RelatedOnlyFieldListFilter
Description
	
RelatedFieldListFilter doesn't fall back to the ordering defined in Model._meta.ordering. 
Ordering gets set to an empty tuple in ​https://github.com/django/django/blob/2.2.1/django/contrib/admin/filters.py#L196 and unless ordering is defined on the related model's ModelAdmin class it stays an empty tuple. IMHO it should fall back to the ordering defined in the related model's Meta.ordering field.
RelatedOnlyFieldListFilter doesn't order the related model at all, even if ordering is defined on the related model's ModelAdmin class.
That's because the call to field.get_choices ​https://github.com/django/django/blob/2.2.1/django/contrib/admin/filters.py#L422 omits the ordering kwarg entirely.

