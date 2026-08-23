# B3_DECISION — retroactive context retirement (FROZEN headline)

**Status: the token-reduction research line is complete. This freezes the B3 headline claim and ends
mechanism-invention experiments.** The next work is a production feasibility spike (B4), not another
oracle/measurement round. Evidence: `docs/b3-findings.md`, `docs/b3.1-safety-findings.md`,
`docs/b3.2-overlap-findings.md`, `docs/b3.3-live-findings.md`; artifacts under `corpus/analysis/b3-*`.

## The frozen headline

> **ContextRuntime's strongest validated mechanism is retroactive context retirement. Offline replay
> estimates roughly 8–11% safe additive token-residency reduction on single-window coding sessions,
> increasing with session length. A small live paired-resume experiment found no obvious degradation
> from removing retired context, but the live experiment was a safety sanity check rather than a direct
> measurement of whole-session token savings.**

Say this, and not "8–11% live-demonstrated saving" — that stronger claim is not yet supported.

## The evidence stack

| step | what it establishes |
|---|---|
| **B3.0** | Retroactive-retirement opportunity **rises with session length**; ~7–15% optimistic single-window opportunity with a ~3–6% mechanically-certain (superseded-only) core. First double-digit lever in the program. |
| **B3.1** | After excluding mechanically-unsafe retirements, **lag-5 safe token-turn saving ≈ 7.6% (<100-turn) and 11.1% (≥100-turn)**, overall ~8.7%; mechanical safety fraction ~98% (short) / ~95.5% (long). |
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
