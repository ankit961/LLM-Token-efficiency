# B3_DECISION — retroactive context retirement (FROZEN headline)

> **Correction (2026-10-05, post-hoc audit):** the frozen headline overstates what the corrected
> artifacts support. The original text is kept.
>
> - **"8–11%".** The 11% end comes only from the pre-correction ≥100-record stratum of
>   `b3-safety-results.json` (n=19, lag-5 safe 11.10%, commit 8db13ba). After the turn correction no
>   session reaches 100 real calls and that stratum no longer exists. The corrected
>   `corpus/analysis/b3-safety-results.json` supports a pooled lag-5 safe NET of **8.30%** (n=60),
>   within the corrected B3.0 ceilings of mech **4.33%** / +tail **10.32%**.
> - **"≈14% tail / 5.6% provably-safe on 60+ calls"** are the unfiltered B3.0 ceilings for that
>   stratum (+tail 13.94 / mech 5.57 in `corpus/analysis/b3-results.json`), not safety-filtered
>   numbers; a safety-filtered 60+ value was never computed. Every 60+ session comes from django-10554
>   or django-11138, so "increasing with session length" is confounded with task.
> - **"Provably-safe" / "mechanically-certain".** Superseded objects are counted as safe but never
>   tested, and supersession keys on the path only, so a full-file Read is "superseded" by an Edit
>   snippet or by a Read at another offset.
> - **"Mechanical safety fraction ~98%"** is an object count. Weighted by token-turns, 8.1% of
>   retired token-turns are unsafe at lag 5 (0.732 / (8.30 + 0.732)).
> - **The B3.0 row** ("~7–15% optimistic … ~3–6% core") uses the pre-correction strata. In the code
>   "+tail" retires every non-superseded tool result, not a selected abandoned tail. The B3.0 gate
>   called "preregistered" was set after the numbers were in (harness docstring). The cost model
>   prices cache writes at the 5-minute 1.25× although the Step-7 sessions used only 1-hour writes,
>   and the batched NET did change with the correction (pooled K=10 raw / cost 9.13 / 6.62 →
>   8.40 / 6.07), contrary to the corrected JSON's note.
> - **B3.3** ("3/3 … ~zero re-read tax") counts the inert pair in its 3/3, measured only Read-tool
>   re-reads (bash re-runs were not detected), and its RETIRED arm in 10554 used 50% more
>   continuation records than FULL; see `docs/b3.3-live-findings.md`.
> - **Provenance.** The corrected B3 JSONs record `code_commit` 5c3a5d5, which predates
>   `corpus/transcript_util.py`; they were produced from an uncommitted working tree.

**Status: the token-reduction research line is complete. This freezes the B3 headline claim and ends
mechanism-invention experiments.** The next work is a production feasibility spike (B4), not another
oracle/measurement round. Evidence: `docs/b3-findings.md`, `docs/b3.1-safety-findings.md`,
`docs/b3.2-overlap-findings.md`, `docs/b3.3-live-findings.md`; artifacts under `corpus/analysis/b3-*`.

## The frozen headline

> **ContextRuntime's strongest validated mechanism is retroactive context retirement. Offline replay
> estimates roughly 8–11% [corrected 2026-10-05: the corrected pooled lag-5 safe NET is 8.30%; the
> 11% end is pre-correction] safe additive token-residency reduction on single-window coding sessions,
> increasing with session length (≈8% pooled; ≈14% tail / 5.6% provably-safe on sessions of 60+ API calls
> [corrected 2026-10-05: unfiltered B3.0 ceilings, not safety-filtered]). A small live paired-resume experiment found no obvious degradation
> from removing retired context, but the live experiment was a safety sanity check rather than a direct
> measurement of whole-session token savings.**

Say this, and not "8–11% live-demonstrated saving" — that stronger claim is not yet supported.

## The evidence stack

| step | what it establishes |
|---|---|
| **B3.0** | Retroactive-retirement opportunity **rises with session length**; ~7–15% optimistic single-window opportunity with a ~3–6% mechanically-certain (superseded-only) core [corrected 2026-10-05: pre-correction strata; corrected pooled mech 4.33% / +tail 10.32%]. First double-digit lever in the program. |
| **B3.1** | After excluding mechanically-unsafe retirements, **lag-5 safe token-turn saving ≈ 8.3% pooled** (corrected to real API-call turns, see `docs/path-to-50.md` §0; sessions of 60+ calls reach ~14% tail / 5.6% provably-safe [corrected 2026-10-05: unfiltered B3.0 ceilings]); mechanical safety fraction ~98% [corrected 2026-10-05: by object count; 8.1% of retired token-turns unsafe at lag 5]. |
| **B3.2** | In multi-window sessions native `/compact` absorbs most of the *raw* saving (unique ≈ 8% of standalone); B3's durable multi-window value is **lossless compaction deferral/avoidance**, capped near the ~16% tool-output share of the prefix. |
| **B3.3** | **Live** paired resume (n=3, CLI-resume hack): removing retired objects did not derail the continuation — 3/3 reached the same fix, ~zero re-read tax. A **safety** check, not a savings measurement (2 active + 1 inert; no clean live cost pairing). |

For context, the mechanisms this beats: search-output reduction ~0.03%, prospective file compaction
~0 net (broke 78% of edits), bash/test ~0.48%, retroactive-file 3.88% gross, and the G1/G2 graph
path (no-go). B3 is qualitatively different — the first result worth calling **product-interesting**.

## Decision

1. **Stop inventing new token-reduction mechanisms.** The research branch has done its job.
2. **The mechanism makes sense and is the right shape:** wait until the evidence says context is *cold*
   (superseded, or untouched for a lag), retire it, keep an exact recovery handle. Unlike prospective
   compression, nothing is dropped while it is still in use.
3. **Next milestone is B4 — Production Context GC feasibility**, a spike, not a measurement:
   `RetirementPlanner` (policy) → `HistoryMutationPlan` → `HistoryMutator` (mechanism), with the two
   halves cleanly separated. The planner is buildable and testable now (`contextruntime/retirement.py`).
4. **The binding product question is the mutation MECHANISM, not another percentage point.** B3.3
   established that the Claude Code subscription client exposes **no runtime API to rewrite prior
   context** — the experiment had to hand-edit a stored resume transcript, which is not a production
   architecture. So on that target the mutator is `Unsupported`; a **gateway** or a **custom agent
   loop** that owns the message array can apply a plan in process.

## Not changed

Frozen B1 (`B1_DECISION.md`), the B2 evidence artifacts, and the G1/G2 closure are untouched. The
0/11 advisory-adoption result stands for what it measured.
