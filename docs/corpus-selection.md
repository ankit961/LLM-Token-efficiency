# Corpus task-selection procedure v1 — DRAFT (for review, NOT executed, NOT locked)

**Purpose:** make the choice of the 50 concrete tasks *mechanical and reproducible*, so task selection
carries no researcher degrees of freedom. This procedure is reviewed and agreed BEFORE it is executed;
executing it fills the `UNRESOLVED` fields in `docs/corpus-plan.md`. Category is assigned purely from
historical diffs, **blind to any run or any observed read/label**.

- **Repository (single, fixed):** `django/django`.
- **Runtime under test:** `obs-runtime-3a-v1` → `484f4b3` (the runner checks out the tag).
- **One HookJournal DB per run** (the task run is the unit).

## Pools

- **S1 — issue-fix pool:** `princeton-nlp/SWE-bench_Verified`, filtered to `repo == "django/django"`.
  Each instance provides `instance_id`, `base_commit`, gold `patch`, `test_patch`, `FAIL_TO_PASS`,
  `PASS_TO_PASS`, `problem_statement`.
- **S2 — refactor pool:** `django/django` merged PRs whose title matches
  `(?i)\b(refactor|cleanup|simplify|reorganize|extract|deduplicate)\b` and NOT `\b(fix|feature|add)\b`,
  that are behavior-preserving (touch ≥2 source files; add no new non-regression test), pinned at the
  PR's merge-base commit. (SWE-bench is issue-FIX data and contains essentially no pure refactors, so
  `refactor` cannot come from S1 — this is the one category sourced from raw PR history.)

## Structural category classifier (computed from the gold diff; first match wins)

Definitions per changed file: **source** = not under `tests/`, `docs/`, and not a config file
(`*.cfg`, `*.ini`, `*.toml`, `setup.py`, `conf.py`, `**/settings*.py`); **test/config** = the rest.
Per instance compute `n_src_files`, `n_src_lines` (added+removed in source files),
`frac_tc_lines` (test/config lines ÷ total changed lines).

| order | category | predicate (on S1 gold patch) |
|--:|---|---|
| 1 | `tests_config` | `frac_tc_lines ≥ 0.70` |
| 2 | `navigation_debugging` | `n_src_files == 1` and `n_src_lines ≤ 3` (locate-and-minimally-fix) |
| 3 | `local_bug_fix` | `n_src_files == 1` and `4 ≤ n_src_lines ≤ 30` |
| 4 | `multi_file_feature` | `n_src_files ≥ 3` |
| — | (unmatched) | not eligible (e.g. 2-source-file fixes) — excluded, counted |
| 5 | `refactor` | **from S2 only** (see above) |

`navigation_debugging`'s "requires broad reading" is an expected *behavior*, not a selection filter —
the observable we select on is the minimal edit (`n_src_lines ≤ 3`). We do not filter on reads.

## Deterministic selection (no peeking)

1. Build S1, classify every instance, drop unmatched/excluded (network-dependent, flaky, or
   over-budget instances are excluded and **counted**, not silently dropped).
2. For each of the 4 S1 categories: sort eligible instances by `instance_id` (lexicographic), take the
   **first 10**. For `refactor`: sort S2 by PR number ascending, take the first 10 meeting the
   signature.
3. **Feasibility gate (BEFORE authoring):** if any category yields < 10 eligible, STOP and bring the
   shortfall back for a *documented* threshold adjustment — decided before any run, never after labels.
4. Fill each category's 10 tasks into that category's 10 preassigned rows in `docs/corpus-plan.md`,
   in ascending `run_order` (the blocked order already fixed which run_order is which category).

## Objective success (`verification_command`; exit 0 == success)

- **S1 categories:** the instance's `FAIL_TO_PASS` tests must go fail→pass AND `PASS_TO_PASS` must stay
  passing (standard SWE-bench evaluation) at `base_commit`.
- **refactor (S2):** BOTH (a) a **change-requiring assertion** derived mechanically from the real PR
  diff — a specific grep/AST post-condition that holds only after the change (e.g. "module M no longer
  imports Z"; "class X no longer defines method Y") — AND (b) the repo's regression tests
  (`PASS_TO_PASS`). A no-op fails (a).

## Task prompt

- S1: the verbatim `problem_statement` (the real issue text) — the only thing the agent sees.
- refactor: a neutral one-paragraph statement of the refactor goal from the PR title/body, **not**
  revealing the diff.

## Provenance stratum

Every row records its source (`S1:swebench-verified` or `S2:django-pr`) so analysis stratifies by
provenance and never conflates the two classes.

## What executing this produces (the NEXT gate, also reviewed before lock)

The 50 concrete `corpus/specs/run-NN.md` specs + filled plan rows + `task_spec_sha256` values. Only
after those are reviewed do we compute `plan_sha256`, flip status to LOCKED, and tag `corpus-plan-v1`.
