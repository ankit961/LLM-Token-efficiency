# Task spec — run-15  (IMMUTABLE)

task_id: django__django-12419
category: fs1_oneline_1f_le3
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test, revision c104f840cc67f8b6eec6f759ebc8b2693d585d4a)
repo_id: django/django
base_commit_sha: 7fa1a93c6c8109010a6ff3f604fda83b604e0e97
fix_shape_evidence: n_src_files=1, src_lines=2   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
# FAIL_TO_PASS entries are preserved VERBATIM from upstream SWE-bench Verified; some read like
# prose but are genuine upstream evaluation directives -- never 'clean' them.
FAIL_TO_PASS:
  - test_middleware_headers (project_template.test_settings.TestStartProjectSettings)
PASS_TO_PASS_count: 0
PASS_TO_PASS_sha256: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Add secure default SECURE_REFERRER_POLICY / Referrer-policy header
Description
	
#29406 added the ability for the SECURE_REFERRER_POLICY setting to set Referrer-Policy, released in Django 3.0.
I propose we change the default for this to "same-origin" to make Django applications leak less information to third party sites.
The main risk of breakage here would be linked websites breaking, if they depend on verification through the Referer header. This is a pretty fragile technique since it can be spoofed.
Documentation: ​https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Referrer-Policy
The MDN support grid is out of date: ​https://caniuse.com/#search=Referrer-Policy

