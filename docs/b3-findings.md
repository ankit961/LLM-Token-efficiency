# B3.0 — Retroactive context-retirement ceiling: offline findings

> **Correction (2026-08-23):** the harness originally counted transcript *records* as turns, but Claude Code stores one API call as several assistant records sharing a `requestId`, so the turn axis was inflated ~1.9× (134 records = 71 real calls) and Σ usage ~1.9×. Fixed in `corpus/transcript_util.merged_records`; the harness now reproduces the CLI-reported usage exactly. **The percentages survive** (pooled mech/+tail 4.33/10.32 vs documented 4.3/10.3; lag-5 safe 0.979/8.30% vs 0.974/8.70%); only the length buckets change — real sessions are 20–180 API calls, so read "≥100 turns" as "≥60 real calls" (13.9% tail / 5.6% mech / 9.3% cost-NET, n=16). See `docs/path-to-50.md` §0.

> **Correction (2026-10-05, post-hoc audit):** the 2026-08-23 note above is only partly right, and
> several statements below are overstated. The original text is kept.
>
> - **What survives and what does not.** Pooled mech / +tail (4.33 / 10.32) do survive. The batched
>   NET columns do not: at K=10 the pooled raw / cost NET moved from 9.13 / 6.62 (this doc's tables)
>   to **8.40 / 6.07** (the corrected `corpus/analysis/b3-results.json`, n-weighted over its two
>   strata), so the JSON's own note "Percentages match the original run" is false for these columns.
>   Every table below (the 17/24/15/4 strata, the giant row, the K sweep) is pre-correction. The
>   corrected JSON has two strata — 0–60 calls (n=44): mech 3.88 / +tail 9.00 / rawNet 6.99 /
>   costNet 4.89; 60+ calls (n=16): 5.57 / 13.94 / 12.28 / 9.31 — and no K sweep. "Real sessions are
>   20–180 API calls" in the note above is also off: 180 was a record count, and CLI `num_turns` spans
>   23–91 (`corpus/analysis/step7-live-results.json`).
> - **"+tail" is not a selective abandoned tail.** `assign_obsolescence` gives every object that is
>   not superseded `tail_turn` = its own turn, so "+tail" retires ALL non-superseded tool results
>   (hence `peak_occupancy_relief_pct` = 100.0 in every stratum), starting with the next request — the
>   first one in which the model would have seen them.
> - **"Provably dead" supersession was never tested.** `_obj_key` keys Read/Edit/Write on the path
>   only, so a full-file Read counts as superseded by a later Edit snippet or by a Read at another
>   offset.
> - **Cost model.** Cost NET prices cache writes at the 5-minute 1.25×, but the Step-7 sessions used
>   only 1-hour writes (3,308,366 `ephemeral_1h` vs 0 `ephemeral_5m` cache-creation tokens in
>   `corpus/analysis/step7-live-results.json`). The 1-hour sensitivity quoted below (5.63% at K=10)
>   exists only in the pre-correction JSON (d83288d) and was not recomputed.
> - **The gate was not preregistered.** The harness docstring (`corpus/b3_context_retirement.py`)
>   says the gate "is set in `docs/b3-findings.md` after the numbers are in". After the turn
>   correction no session reaches 100 calls, and the gate was remapped to 60+.
> - **Length trend is confounded with task:** every session with ≥60 calls comes from django-10554 or
>   django-11138.
> - **Provenance.** The corrected JSON's `code_commit` (5c3a5d5) predates `corpus/transcript_util.py`
>   (added in 8a27160), so the corrected numbers came from an uncommitted working tree.


**Run 2026-08-22, ZERO Claude quota. No live history transformer built.** Offline replay over the 60
Step-7 django sessions (single-window, 37–180 turns) plus one very long real session (this
workstream's own transcript, 2,365 turns) as a caveated extreme point. Harness
`corpus/b3_context_retirement.py`; per-session rows, strata, batch sweep, and cost sensitivity in
`corpus/analysis/b3-results.json`.

## What B3.0 measures (and how it generalizes B2.v2)

B2.v2 (`retroactive_replay`) measured one thing: FILE reads sitting full in the prefix after the agent
last touched them (3.88% mean gross). B3.0 generalizes "retirable object" from files to **every tool
result** — reads, greps, globs, bash outputs, edit/write diffs — and prices the two things B2.v2
omitted: which retirements are *provably* safe, and what a prefix rewrite actually *costs*.

Each tool result enters the prefix at its turn and is cache-READ every later turn until retired
(`Value(x)=Tokens(x)×RemainingTurns(x)`). An object becomes retirable when:

- **SUPERSEDED (mechanical, provably dead):** a later object touches the same file path, or repeats
  the identical grep/glob/bash invocation. The earlier view is stale from the later turn — the file
  was re-read or edited, the command re-run. Safe to drop with zero reasoning loss. [corrected
  2026-10-05: never tested; path-only keys make a full Read "superseded" by an Edit snippet or a Read
  at another offset]
- **ABANDONED TAIL (speculative):** the last object touching a path/key, never revisited. Assumed dead
  after its turn. [corrected 2026-10-05: in the code this is every non-superseded object, retired at
  its own turn; see top] **Not** provable — the agent may still reason from it — so it is reported as a
  separate, optimistic layer, not folded into the safe number.

## Two currencies (the modelling crux)

- **Raw T_total** (context-window pressure / turns-to-limit): retiring object *o* at turn *b* saves
  `size(o)·(T − b)` cache-read tokens. A prefix **rewrite is ~free here** — the invalidated suffix is
  counted once as cache_creation instead of cache_read that turn, the same raw token count. So the raw
  NET ≈ the gross, and batching barely changes it.
- **Cost ($, cache-weighted):** cache_read is cheap (0.1× base), cache_creation dear (1.25× 5-min TTL,
  2.0× 1-hour). Retiring saves `0.1·gross`; each compaction event re-creates the invalidated suffix at
  `(write−read)×` once. `COST NET = 0.1·gross − (write−0.1)·Σ_events suffix`. Positive only when the
  session is long (gross ∝ T) and compaction is batched coarsely (few events). This is the real
  "cache-read savings minus cache-rewrite cost."

## Results — the ceiling GROWS with session length (every column, monotonic)

[2026-10-05: this table is pre-correction (record-turn strata); the corrected strata are in the note
at top.] Mean over 60 single-window django sessions, bucketed by turn count. `mech%` = provably-safe
supersession ceiling; `+tail%` = adding the speculative abandoned tail; `rawNet%`/`costNet%` =
realized under batched compaction (every-10-turns, +tail), as % of T_total and of the $-weighted cache
baseline.

| turns | n | µturns | **mech%** | **+tail%** | rawNet% | costNet% |
|---|---:|---:|---:|---:|---:|---:|
| 0–60 | 17 | 47 | 3.20 | 7.02 | 5.60 | 3.79 |
| 60–100 | 24 | 74 | 4.39 | 10.15 | 8.85 | 6.22 |
| 100–150 | 15 | 127 | 5.03 | 13.17 | 12.24 | 9.28 |
| 150+ | 4 | 162 | 6.12 | 14.95 | 14.14 | 11.06 |
| *giant (2365t)* | 1 | 2365 | *6.61* | *20.21* | *20.15* | *15.25* |

The giant row is **italic because it is a multi-window session** — its T_total (1.17B) far exceeds one
200k context window, i.e. native compaction already fired repeatedly. My single-prefix model therefore
**overstates** it (it counts residency of objects native compaction already evicted). It bounds the
trend from above, it does not measure a real single-window ceiling.

**Batching trade-off** (mean over the 60; +tail; 5-min write=1.25×) [2026-10-05: pre-correction; the
corrected K=10 pooled values are 8.40 raw / 6.07 cost]:

| every-K turns | µevents | rawNet% | costNet% |
|---|---:|---:|---:|
| 1 | 44 | 10.34 | 7.60 |
| 5 | 17 | 9.81 | 6.98 |
| 10 | 8 | 9.13 | 6.62 |
| 20 | 4 | 7.96 | 5.93 |
| 50 (≈once) | 1.2 | 4.54 | 3.49 |

The knee is **K≈5–10**: ~8 compaction events keep ~90% of the benefit. Compacting once (K=50) collapses
to 4.5%/3.5% — you must retire *during* the session, not at the end. Under the 1-hour cache's steeper
write premium (2.0×), cost NET at K=10 is 5.63% (vs 6.62%) — robust to cache TTL. [2026-10-05:
pre-correction value, not recomputed; the corpus used 1-hour writes throughout, so this is the
relevant pricing, not a sensitivity case]

## Verdict against a preregistered gate

[corrected 2026-10-05: the gate was set after the numbers were in (harness docstring); see top]

> **Gate (proceed to a scoped B3.1 build only if):** on realistic long sessions (100+ turns), the
> provably-safe `mech` NET ≥ 5% **or** the `+tail` NET ≥ 12%, **and** cost NET stays positive under
> coarse batching.

**Met on long sessions.** At 100–150 turns: mech 5.03%, +tail 13.17%, cost 9.28% (all clear). At 150+:
6.12% / 14.95% / 11.06%. This is the **first lever in the whole program to reach double digits** —
compare B1 search 0.03%, prospective-file ~0 net, bash 0.48%, and the G1/G2 graph no-go. And it is the
*right shape*: value compounds with length, exactly where token cost actually hurts.

**But three honest deductions keep it from being a slam-dunk build order:**

1. **The safe core is modest; the double digits need the speculative tail.** Provable supersession is
   only 3–6% single-window. The 7–15% requires retiring the abandoned tail, which is *not* provably
   safe — it needs a staleness policy (retire after L turns untouched) whose realized value sits
   *between* the mech floor and the +tail ceiling, and whose safety must be shown (the B2.3-style
   "did retirement ever break a later step" test, but for all object kinds).
2. **It competes with native compaction on exactly the long sessions where it is biggest.** The giant
   session *proves* native `/compact` already retires context past one window. B3's marginal value is
   whatever native compaction leaves on the table. Its real pitch is therefore **qualitative** —
   surgical, lossless retirement of *provably* dead objects to defer or shrink lossy summarization —
   not "beat native compaction on raw %."
3. **The saving is a counterfactual.** `size·(T−b)` is what cache_read *would* have been; no live run
   confirms it, and the raw-token view (rewrite ~free) only holds if the transformer overlaps cleanly
   with native caching and compaction rather than fighting them.

## Skeptical caveats

- **One task family** (django SWE-bench) and **n=4** in the 150+ bucket. The trend is clean and
  monotonic, but the slope/saturation are not pinned down; the giant point is multi-window (overstated).
- **Object-size ≈ tool-result tokens**; conversation text and system/tool-def residency are excluded
  (they are not tool-result objects), so this is a ceiling on the *tool-output* slice of the prefix.
- **Supersession is conservative** (same path / identical command). A semantic "this result is now
  irrelevant" would retire more but is not mechanical — deliberately out of scope.
- **Cost model uses list cache multipliers** (0.1× / 1.25× / 2.0×); real billing and the interaction
  with the 5-min vs 1-hour TTL choice would shift the cost NET, not the raw NET.

## Recommendation — STOP here for review (no live transformer)

B3.0 has done its job: the oracle NET ceiling is known. Retroactive context retirement is **the most
promising token lever this program has found** — double-digit potential that grows with length — but
its *provably-safe* core is 3–6%, its double digits ride a not-yet-safe tail, and it overlaps native
compaction. Per the standing directive, the live history transformer is **not** built. The decision the
user now holds: (a) proceed to a scoped **B3.1** — a staleness-policy safety replay (extend B2.3's
"never break a later step" test to all object kinds) + a cache-accurate NET model that accounts for
native-compaction overlap — before any transformer; or (b) treat 3–6% provable / ~10% speculative as
insufficient against the build complexity and close the token-saving thesis here.

Frozen B1, the B2 evidence artifacts, and the G1/G2 closure were not touched. No live run; no new quota.
