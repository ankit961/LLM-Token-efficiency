# Task spec — run-36  (IMMUTABLE)

task_id: django__django-12155
category: fs5_multi_ge2f
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: e8fcdaad5c428878d0a5d6ba820d957013f75595
fix_shape_evidence: n_src_files=2, src_lines=23   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_parse_rst_with_docstring_no_leading_line_feed (admin_docs.test_utils.TestUtils)
PASS_TO_PASS_count: 6
PASS_TO_PASS_sha256: sha256:88cbc8f98a4f8eedad180fbe17dc59aa454e3d18b61cf864266decba16ea7353

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
docutils reports an error rendering view docstring when the first line is not empty
Description
	
Currently admindoc works correctly only with docstrings where the first line is empty, and all Django docstrings are formatted in this way.
However usually the docstring text starts at the first line, e.g.:
def test():
	"""test tests something.
	"""
and this cause an error:
Error in "default-role" directive:
no content permitted.
.. default-role:: cmsreference
The culprit is this code in trim_docstring:
indent = min(len(line) - len(line.lstrip()) for line in lines if line.lstrip())
The problem is that the indentation of the first line is 0.
The solution is to skip the first line:
indent = min(len(line) - len(line.lstrip()) for line in lines[1:] if line.lstrip())
Thanks.

