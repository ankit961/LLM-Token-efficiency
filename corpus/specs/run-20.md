# Task spec — run-20  (IMMUTABLE)

task_id: django__django-11276
category: fs4_large_1f_16_60
source: SWE-bench_Verified (princeton-nlp/SWE-bench_Verified, split=test)
repo_id: django/django
base_commit_sha: 28d5262fa3315690395f04e3619ed554dbaf725b
fix_shape_evidence: n_src_files=1, src_lines=26   # blind to reads

## Objective success (machine-checked; NOT agent self-report)
verification: at base_commit, apply the agent's final patch; the FAIL_TO_PASS tests must go
              fail->pass AND all PASS_TO_PASS tests must remain passing (standard SWE-bench eval).
FAIL_TO_PASS:
  - test_make_list02 (template_tests.filter_tests.test_make_list.MakeListTests)
  - test_password_help_text (auth_tests.test_forms.UserCreationFormTest)
  - test_url_split_chars (template_tests.filter_tests.test_urlize.FunctionTests)
  - test_wrapping_characters (template_tests.filter_tests.test_urlize.FunctionTests)
  - test_addslashes02 (template_tests.filter_tests.test_addslashes.AddslashesTests)
  - test_title1 (template_tests.filter_tests.test_title.TitleTests)
  - test_urlize01 (template_tests.filter_tests.test_urlize.UrlizeTests)
  - test_urlize06 (template_tests.filter_tests.test_urlize.UrlizeTests)
  - test_html_escaped (forms_tests.widget_tests.test_clearablefileinput.ClearableFileInputTest)
  - test_url12 (template_tests.syntax_tests.test_url.UrlTagTests)
  - test_initial_values (model_forms.tests.ModelFormBasicTests)
  - test_m2m_initial_callable (model_forms.tests.ModelFormBasicTests)
  - test_multi_fields (model_forms.tests.ModelFormBasicTests)
  - test_runtime_choicefield_populated (model_forms.tests.ModelFormBasicTests)
  - test_no_referer (view_tests.tests.test_csrf.CsrfViewTests)
  - test_escape (utils_tests.test_html.TestUtilsHtml)
  - test_escapejs (utils_tests.test_html.TestUtilsHtml)
  - test_escaping (forms_tests.tests.test_forms.FormsTestCase)
  - test_templates_with_forms (forms_tests.tests.test_forms.FormsTestCase)
  - An exception report can be generated without request
  - A simple exception report can be generated
  - A message can be provided in addition to a request
  - test_methods_with_arguments_display_arguments_default_value (admin_docs.test_views.TestModelDetailView)
  - Safe strings in local variables are escaped.
  - test_message_only (view_tests.tests.test_debug.ExceptionReporterTests)
  - test_request_with_items_key (view_tests.tests.test_debug.ExceptionReporterTests)
PASS_TO_PASS_count: 548
PASS_TO_PASS_sha256: sha256:1fc7b3e0ddf680181c7152f7ec288cbc6e21146379aaae46497ca11c09107250

## Fixed run parameters (frozen before the run)
turn_or_walltime_budget: 40 agent turns OR 20 min wall, whichever first
permission_mode: bypassPermissions
headless_or_interactive: headless
runtime: obs-runtime-3a-v1 (484f4b3)

## Prompt (verbatim issue text the agent sees)
Use Python stdlib html.escape() to in django.utils.html.escape()
Description
	
The function django.utils.html.escape() partially duplicates the Python stdlib function html.escape(). We can replace this duplication with wider community developed version.
html.escape() has been available since Python 3.2:
​https://docs.python.org/3/library/html.html#html.escape
This function is also faster than Django's. As Python bug ​https://bugs.python.org/issue18020 concludes, using .replace() can be faster than .translate(). This function gets called numerous times when rendering templates. After making the change locally, I saw the following improvement:
master:
$ python -m timeit -s 'from django.utils.html import escape' 'escape(copyright)'
50000 loops, best of 5: 4.03 usec per loop
branch:
$ python -m timeit -s 'from django.utils.html import escape' 'escape(copyright)'
100000 loops, best of 5: 2.45 usec per loop
One small concern, html.escape() converts ' to &#x27 rather than &#39. These values are functionally equivalent HTML, but I'll mention it as a backwards incompatible change as the literal text has changed

