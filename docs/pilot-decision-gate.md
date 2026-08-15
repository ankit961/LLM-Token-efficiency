# Pilot 3–4 decision gate — PREREGISTERED (recorded BEFORE running)

Decides A (proceed with frozen v1 corpus) vs B (new observation contract) after 4 total pilots. The
key quantity is missed *source context*, not the raw unknown-Bash count: a `python runtests.py` is
execution evidence, not a missed code read.

## Pilot tasks (mechanically chosen, both outside corpus-plan-v1)

- Pilot 3 (small / single-file): **django__django-10097** (fs1_oneline_1f_le3), base `b9cf764be62e`.
- Pilot 4 (large / multi-file):  **django__django-11532** (fs5_multi_ge2f), base `a5308514fb4b`.
- (Pilots 1–2 already run: django-10999 fs3, django-11400 fs5.)

## Per unrecognized Bash call, classify into three buckets (regex on the command)

- **source_materialization** — grep / rg / git grep / egrep / fgrep / ag / ack / find / ls / awk / a
  cat|head|tail|sed pipeline that cr-hook marked unknown (exposes code/text/paths).
- **execution_evidence** — runtests.py / pytest / tox / manage.py / `python`/`python -m` execution /
  make / flake8 / black / mypy / pylint / pip / npm / node.
- **other_or_ambiguous** — everything else (cd, echo, mkdir, git status/diff/log, complex pipes).

Classification order: execution_evidence first, then source_materialization, then other.

## Measured per pilot (raw pilot-only shell tap + the cr-hook journal)

recognized Read-tool reads (count + model-visible tokens); per-bucket unrecognized-Bash calls +
model-visible tokens (from PostToolBatch, est chars/4); edit/precondition/verification labels;
W={8,16,32,∞}.

MissedContextTokenShare = T(unrecognized source_materialization) /
                          ( T(recognized reads) + T(unrecognized source_materialization) ).

## PREDECLARED A/B thresholds (do not adjust after seeing results)

- **Choose A (proceed frozen v1)** iff ALL hold:
  1. aggregate MissedContextTokenShare ≤ 0.30, AND
  2. ≥ 25 classifiable Read-tool reads across the 4 pilots, AND
  3. ≥ 3 of 4 pilots have a nontrivial read population (≥ 3 reads each).
- **Choose B (new hook_schema 0.4.0 contract)** iff EITHER:
  1. aggregate MissedContextTokenShare > 0.30, OR
  2. ≥ 2 of 4 pilots produce essentially no classifiable reads (< 3) despite meaningful navigation
     (≥ 5 unrecognized source_materialization calls).

## If B: preserve v1 immutably; do NOT redraw tasks

Keep `obs-runtime-3a-v1`→484f4b3 and `corpus-plan-v1`→3d0a0d6 as immutable v1. Create hook_schema
0.4.0 with representation-typed shell (search_materialization / path_listing / file_materialization /
execution_evidence / unknown — `python runtests.py` stays execution_evidence, never a fake read), a new
`obs-runtime-3a-v2`, and `corpus-plan-v2` reusing the **exact same 50 task identities and run order**.
