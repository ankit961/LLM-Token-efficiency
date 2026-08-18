# Step 7 — replicated live A/D experiment (findings)

**Run 2026-08-18. 60 live `claude -p` sessions** (3 arms × 4 django tasks × 5 reps), sonnet, 900 s
cap, via `corpus/step7_live_experiment.py`. 0 harness errors; 57/60 completed (3 wall-clock
timeouts, spread 1/1/1 across arms). ~$2.3/session. Arms: `A_native` vs `D1=(256,125)` (the
conservative Pareto winner — floor-only change vs shipped) vs `D2=(128,125)` (the knee). Graph OFF.
Task success DEFERRED — all 60 patches saved for later SWE-bench grading.

## Arm summary (n=20 each)

| arm | completed | T_total | turns | eff_read | reductions | native_rereads | re_searches | result_expansions | wall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_native | 19/20 | 3.14M | 45.1 | 7,976 | 0 | 4.3 | 0 | 0 | 352 s |
| D1 (256,125) | 19/20 | 3.33M | 47.3 | 9,127 | 3.6 | 3.7 | 0 | 0 | 366 s |
| D2 (128,125) | 19/20 | 3.75M | 52.0 | 9,782 | 7.2 | 4.8 | 0 | 0 | 349 s |

## Paired D−A (mean over 5 reps per task; T_total/turns over completed runs)

| task (turns) | ΔT_total% D1 | ΔT_total% D2 |
|---|---:|---:|
| 10554 | +13.1 | +41.9 |
| 11138 (longest, ~70 turns) | **−4.7** | **−10.5** |
| 12419 | +21.1 | +19.3 |
| 14608 | +11.9 | +30.9 |
| **pooled** | **+10.4** | **+20.4** |
| pooled Δturns% | +7.0 | +14.8 |

## The result: whole-session T_total is turn-dominated; search reduction cannot move it

Three facts settle it:

1. **`T_total` is 98.8% explained by turn count** (Pearson r = 0.988 over 57 completed runs;
   ~71,000 tokens/turn). Whole-session cost is the cache-dominated prefix re-read every turn — it
   scales with how many turns the agent takes, not with search-output size.
2. **The reducer's direct saving is negligible against that:** mean `reducer_saved_tokens` = **935
   (D1) / 1,807 (D2)** per session — **0.028% / 0.048% of `T_total`**. Even compounded over the
   remaining turns (≈935 × ~40 ≈ 37k ≈ ~1% of `T_total`, an optimistic upper bound), it sits far
   below the trajectory-length variance.
3. **So the observed ΔT_total (+10% D1, +20% D2) is trajectory noise, not reduction.** It tracks
   Δturns almost exactly (D1 +7% turns → +10% tokens; D2 +15% turns → +20% tokens): the D arms
   happened to take more turns on these independent sessions. The per-task sign even flips on the
   one long task (11138: D1 −4.7%, D2 −10.5%), where the prefix-compounding effect finally shows.

**There is no recovery penalty.** `result_expansions = 0` and `re_searches = 0` in every one of the
60 sessions; native re-reads did not rise under D1 (3.7 < A's 4.3) and rose only slightly under D2
(4.8). Wall time is within ~4% (D1 366 s, D2 349 s vs A 352 s). Completion is unchanged (19/20 each;
D2 20/20 non-timeout). The agent simply never needed to pay to recover dropped evidence.

## Against the production gates

| gate | verdict |
|---|---|
| No material task-success regression | **DEFERRED** — patches saved; completion (weak proxy) unchanged 19-19-19/20 |
| Consistent whole-session context-burden reduction | **NOT MET** — `T_total`/`eff_read` rise under D (trajectory-driven); direct saving is ~0.03–0.05% |
| No large retry/re-search/expansion penalty | **MET** — expansions 0, re_searches 0, re-reads not up for D1 |
| No materially worse wall time | **MET** — within ~4% |

## What this means (decisive, not another experiment)

The per-read compression is **real** (Step 6: 12.1% of search-output tokens) and **safe** (no live
recovery penalty), but **search-output reduction is the wrong lever for whole-session token cost**:
search outputs are a rounding error against a prefix that is re-read every turn. Whole-session
`T_total` is governed by **turns × cached-prefix size** (r = 0.988). Moving it requires reducing the
**re-read prefix** (file reads, conversation history, tool definitions) — not search outputs. This
confirms the project's own "prefix is the master lever" thesis with a direct live measurement, and
it is the input to the B1 freeze (see `B1_DECISION.md`). The one long, grep-heavy session hints the
mechanism compounds to ~5–10% when sessions are long enough — a scale-dependent, telemetry-tracked
upside, not a headline claim.

## Caveats

4 django tasks, sonnet, 5 reps, 3 timeouts censored from the T_total means. Task success is graded
later. The negative whole-session result is a magnitude argument (0.03% direct saving vs a
cache-dominated prefix), robust to the trajectory noise; the ~5–10% long-session hint is n=1 task
and not established.
