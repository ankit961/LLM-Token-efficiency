# Task spec — run-41  (IMMUTABLE)

task_id: django__django-12325
category: fs5_multi_ge2f
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 29c126bb349526b5f1cd78facbe9f25906f18563
fix_shape_evidence: n_src_files=2, src_lines=8   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_clash_parent_link (invalid_models_tests.test_relative_fields.ComplexClashTests)
  - test_onetoone_with_parent_model (invalid_models_tests.test_models.OtherModelTests)
PASS_TO_PASS_count: 201
PASS_TO_PASS_sha256: sha256:d2d40b0b46ab1e2d03181fd4f7edee00a4d2292723f75eb195d8179ff7f86288

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
pk setup for MTI to parent get confused by multiple OneToOne references.
Description
	
class Document(models.Model):
	pass
class Picking(Document):
	document_ptr = models.OneToOneField(Document, on_delete=models.CASCADE, parent_link=True, related_name='+')
	origin = models.OneToOneField(Document, related_name='picking', on_delete=models.PROTECT)
produces django.core.exceptions.ImproperlyConfigured: Add parent_link=True to appname.Picking.origin.
class Picking(Document):
	origin = models.OneToOneField(Document, related_name='picking', on_delete=models.PROTECT)
	document_ptr = models.OneToOneField(Document, on_delete=models.CASCADE, parent_link=True, related_name='+')
Works
First issue is that order seems to matter?
Even if ordering is required "by design"(It shouldn't be we have explicit parent_link marker) shouldn't it look from top to bottom like it does with managers and other things?

