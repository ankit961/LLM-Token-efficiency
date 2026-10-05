# Step 7 — replicated live A/D experiment (findings)

> **Correction (2026-10-05, post-hoc audit):** several figures below do not match
> `corpus/analysis/step7-live-results.json`. The original text is kept; the corrected values are:
>
> - **Completion and cost.** The 3 non-completed runs are 2 `budget_walltime` (11138 A_native rep4,
>   11138 D1 rep2) and 1 `error` (14608 D2 rep4), not 3 timeouts. Mean `total_cost_usd` is **$1.64**
>   per session (58 runs report a cost; total $95.14), not ~$2.3.
> - **Arm table, D2 row.** 3.75M and 52.0 turns include the errored run, unlike the A and D1 rows
>   and the paired table ("over completed runs"); completed-only D2 is **3.79M / 52.2**.
> - **"Pooled" rows.** +10.4/+20.4 (ΔT_total) and +7.0/+14.8 (Δturns) are means of the per-task
>   ratios. Pooled over completed runs (ratio of arm means), ΔT_total is **+6.4% (D1) / +20.8% (D2)**
>   and Δturns **+4.9% / +15.8%**. The conclusion that ΔT_total tracks Δturns is unchanged.
> - **"98.8% explained by turn count".** Pearson r = 0.988 over 57 completed runs, so r² = **0.976**
>   (97.6% of variance). "~71,000 tokens/turn" is ΣT_total/Σturns (70,914); the OLS slope is ~110k
>   tokens per extra turn.
> - **"No recovery penalty" / `re_searches = 0`.** Only exact repeats are 0
>   (`exact_search_repeat_count`). The broader metric designated in `docs/step5-abc-experiment.md`,
>   `repeated_scope_count`, rose from **55 (A) to 95 (D1) to 110 (D2)** (sums over 20 runs; means
>   2.75 / 4.75 / 5.5). The audit's task-stratified permutation test gives p ≈ 0.005 for D2 vs A
>   (D1 p ≈ 0.08), and per 100 turns D2 is up on all 4 tasks. The metric was computed into the JSON but
>   not reported here or in `B1_DECISION.md`. Two further limits: A_native sets no `CR_REDUCE_FLOOR`,
>   so its decision log (and search fingerprints) covers only outputs ≥400 tok while the D arms log
>   ≥125; and the recovery MCP config has no `alwaysLoad`, so `result_expansions = 0` may partly
>   reflect `context_expand` not being loaded (plausible, not shown for Step 7). The gate verdict
>   "MET" below rests on exact repeats and expansions only.
> - **Task success.** Never graded: `task_resolved` is null for all 60 runs.
> - **Cap.** The 900 s cap stated here is what ran; the design doc's 600 s is not.

**Run 2026-08-18. 60 live `claude -p` sessions** (3 arms × 4 django tasks × 5 reps), sonnet, 900 s
cap, via `corpus/step7_live_experiment.py`. 0 harness errors; 57/60 completed (3 wall-clock
timeouts, spread 1/1/1 across arms) [corrected 2026-10-05: 2 `budget_walltime` + 1 `error`]. ~$2.3/session
[corrected 2026-10-05: $1.64]. Arms: `A_native` vs `D1=(256,125)` (the
conservative Pareto winner — floor-only change vs shipped) vs `D2=(128,125)` (the knee). Graph OFF.
Task success DEFERRED — all 60 patches saved for later SWE-bench grading [2026-10-05: still ungraded].

## Arm summary (n=20 each)

| arm | completed | T_total | turns | eff_read | reductions | native_rereads | re_searches | result_expansions | wall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_native | 19/20 | 3.14M | 45.1 | 7,976 | 0 | 4.3 | 0 | 0 | 352 s |
| D1 (256,125) | 19/20 | 3.33M | 47.3 | 9,127 | 3.6 | 3.7 | 0 | 0 | 366 s |
| D2 (128,125) | 19/20 | 3.75M [corrected 2026-10-05: 3.79M completed-only] | 52.0 [corrected 2026-10-05: 52.2] | 9,782 | 7.2 | 4.8 | 0 | 0 | 349 s |

## Paired D−A (mean over 5 reps per task; T_total/turns over completed runs)

| task (turns) | ΔT_total% D1 | ΔT_total% D2 |
|---|---:|---:|
| 10554 | +13.1 | +41.9 |
| 11138 (longest, ~70 turns) | **−4.7** | **−10.5** |
| 12419 | +21.1 | +19.3 |
| 14608 | +11.9 | +30.9 |
| **pooled** [corrected 2026-10-05: mean of per-task %] | **+10.4** [corrected 2026-10-05: pooled +6.4] | **+20.4** [corrected 2026-10-05: pooled +20.8] |
| pooled Δturns% [corrected 2026-10-05: mean of per-task %] | +7.0 [corrected 2026-10-05: pooled +4.9] | +14.8 [corrected 2026-10-05: pooled +15.8] |

## The result: whole-session T_total is turn-dominated; search reduction cannot move it

Three facts settle it:

1. **`T_total` is 98.8% explained by turn count** [corrected 2026-10-05: r = 0.988, so r² = 0.976 —
   97.6% explained] (Pearson r = 0.988 over 57 completed runs;
   ~71,000 tokens/turn [corrected 2026-10-05: ΣT/Σturns; the OLS slope is ~110k]). Whole-session cost is the cache-dominated prefix re-read every turn — it
   scales with how many turns the agent takes, not with search-output size.
2. **The reducer's direct saving is negligible against that:** mean `reducer_saved_tokens` = **935
   (D1) / 1,807 (D2)** per session — **0.028% / 0.048% of `T_total`**. Even compounded over the
   remaining turns (≈935 × ~40 ≈ 37k ≈ ~1% of `T_total`, an optimistic upper bound), it sits far
   below the trajectory-length variance.
3. **So the observed ΔT_total (+10% D1, +20% D2) is trajectory noise, not reduction.** It tracks
   Δturns almost exactly (D1 +7% turns → +10% tokens; D2 +15% turns → +20% tokens) [corrected
   2026-10-05: per-task means; pooled, D1 +4.9% turns → +6.4% tokens, D2 +15.8% → +20.8%]: the D arms
   happened to take more turns on these independent sessions. The per-task sign even flips on the
   one long task (11138: D1 −4.7%, D2 −10.5%), where the prefix-compounding effect finally shows.

**There is no recovery penalty.** [corrected 2026-10-05: not established — exact repeats are 0, but
`repeated_scope_count` rose 55 → 95 → 110; see the correction at top] `result_expansions = 0` and `re_searches = 0` in every one of the
60 sessions; native re-reads did not rise under D1 (3.7 < A's 4.3) and rose only slightly under D2
(4.8). Wall time is within ~4% (D1 366 s, D2 349 s vs A 352 s). Completion is unchanged (19/20 each;
D2 20/20 non-timeout). The agent simply never needed to pay to recover dropped evidence.

## Against the production gates

| gate | verdict |
|---|---|
| No material task-success regression | **DEFERRED** — patches saved; completion (weak proxy) unchanged 19-19-19/20 [2026-10-05: never graded] |
| Consistent whole-session context-burden reduction | **NOT MET** — `T_total`/`eff_read` rise under D (trajectory-driven); direct saving is ~0.03–0.05% |
| No large retry/re-search/expansion penalty | **MET** — expansions 0, re_searches 0, re-reads not up for D1 [corrected 2026-10-05: rests on exact repeats only; `repeated_scope_count` rose 55 → 95 → 110] |
| No materially worse wall time | **MET** — within ~4% |

## What this means (decisive, not another experiment)

The per-read compression is **real** (Step 6: 12.1% of search-output tokens [2026-10-05: possibly
biased low by pre-reduced transcripts, see `docs/step6-paired-replay-findings.md`]) and **safe** (no live
recovery penalty [corrected 2026-10-05: not established; see top]), but **search-output reduction is the wrong lever for whole-session token cost**:
search outputs are a rounding error against a prefix that is re-read every turn. Whole-session
`T_total` is governed by **turns × cached-prefix size** (r = 0.988). Moving it requires reducing the
**re-read prefix** (file reads, conversation history, tool definitions) — not search outputs. This
confirms the project's own "prefix is the master lever" thesis with a direct live measurement, and
it is the input to the B1 freeze (see `B1_DECISION.md`). The one long, grep-heavy session hints the
mechanism compounds to ~5–10% when sessions are long enough — a scale-dependent, telemetry-tracked
upside, not a headline claim.

## Caveats

4 django tasks, sonnet, 5 reps, 3 timeouts censored from the T_total means [corrected 2026-10-05:
2 timeouts + 1 error; the arm table's D2 row still includes the error run]. Task success is graded
later [2026-10-05: not yet graded]. The negative whole-session result is a magnitude argument (0.03% direct saving vs a
cache-dominated prefix), robust to the trajectory noise; the ~5–10% long-session hint is n=1 task
and not established.
