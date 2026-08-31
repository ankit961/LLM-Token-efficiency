# B8 — Live validation of cache-aligned retirement: preregistered protocol

**Status: PREREGISTERED, NOT RUN. Zero quota spent so far; no spend until the budget line below
is explicitly approved.** B7's interactive dollar result (~−60% pooled, modeled) cannot be tested
on B6-style sessions — the gated scheduler correctly never fires there. B8 manufactures the
long/interactive regime under experimental control and judges the live result against a
**model-predicted band frozen here before any spend**. If live lands inside the band, the
calibrated model — and with it the ~60% tail claim — inherits live credibility.

## Design

One conversation = **three sequential graded django tasks** (frozen B6 roster: 16485, 16527,
16901), each in its own worktree under a shared parent cwd, chained with `claude -p` /
`claude -p --resume`, with a **real 65-minute idle gap** between tasks (strictly beyond the 1-hour
cache TTL, creating genuine expiry windows). Context accumulates across the whole session
(~85k tokens by task 3).

Arms per pair (same tasks, same order, same gaps; N and T run concurrently):

- **N**: native chained session.
- **T**: identical, through ONE gateway-proxy process for the entire session —
  `CR_GATEWAY_MODE=enforce`, thinking keep-1, **`CR_GATEWAY_CACHE_ALIGN=gated`** (persistent
  fired set; fires only on cold-start / ttl-gap / break-even). The proxy singleton fix
  (`gateway_singleton`) is required and landed with this harness.

## Preregistered endpoints

Primary — list-price cost ratio from transcript usage (the B7 accounting, observed live):

    BITE = 0.1·cache_read + 2.0·cache_creation + 1.0·uncached_input + 5.0·output
    R$ = Σ BITE_T / Σ BITE_N     (paired; pooled over pairs)

**Predicted by the calibrated B7 model for THIS exact shape (chained real B6 timelines + gaps):
gated −17.1% (cold_gap −16.6%, oracle −16.9%, unaligned −9.0%).**

    VALIDATION GATE: live pooled R$ lands in [−22%, −12%]  → model VALIDATED in-regime
    (band = prediction ± the model's demonstrated ~7% creation-error margin + estimator noise)
    live in [−12%, 0%]   → direction confirmed, magnitude over-predicted; recalibrate before
                           quoting any interactive dollar number
    live > 0% (T costs more) → gated scheduling REFUTED live; align default stays off

Secondary: CLI-reported cost; Σ P residency (predicted −21%); gateway fires **by reason**
(prediction exercises all three: cold-start, 2 ttl-gaps, break-even), persistent_applied,
fallback_original (>2/session halts the run); per-task F2P+P2P grading, treatment non-inferior
(successes_T ≥ successes_N − 1 over all graded task-instances; B6 conventions incl. official
test-file reset).

## Replication, budget, wall-clock

- **3 pairs = 6 sessions** (each 3 tasks + 2×65-min gaps). Per-chunk budget cap $2.50.
- **Expected ≈ $17–20-equivalent on the subscription; hard cap $25.**
- Wall-clock ≈ 3–3.5 h per pair (N and T concurrent), ≈ 10–11 h total — an overnight run
  (sleep-inhibited). Incremental saves; a killed run resumes without repeating completed chunks.

## Honesty rules

- The predicted band above is frozen BEFORE the first paid session and may not be revised after.
- Pooled and per-pair R$ both reported; timed-out or budget-capped chunks disclosed, their pair
  excluded from R$ and counted in the quality table.
- The chained-session shape is a manufactured proxy for interactive work; the ~60% B7 tail claim
  remains modeled either way — B8 validates the MODEL in its firing regime, not the tail number
  directly. That distinction survives into any external claim.

**STOP: awaiting explicit budget approval (≈$17–20, cap $25) and any replication adjustments.**
