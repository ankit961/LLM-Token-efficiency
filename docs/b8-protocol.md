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

---

## v2 (2026-08-28, preregistered before its first paid session; v1 above ran and is CONFOUNDED)

**v1 outcome, kept honest:** 2 pairs completed ($16.65-equiv; 12/12 graded task-instances
succeeded in both arms) but the primary endpoint read +139.9%/+35.5% — **outside the band, and
invalid as a test**: a previously unknown client behavior (custom `ANTHROPIC_BASE_URL` ⇒ MCP
tool-schema deferral DISABLED) inflated the T arm by ~43k tokens/call from request 1
(84,676 vs 41,554 first-request tokens) plus repeated full-prefix rewrites on tool-list changes —
six read=0 rewrites, one on a request with zero gateway mutations active. The scheduler itself
behaved exactly as designed (fires only cold-start/ttl-gap, monotone persistent set, 0 fallbacks).
Post-hoc removal of the de-deferral mass lands T0 at ≈ −16%, inside the original band — evidence
the prediction machinery is sound, but per preregistration rules a post-hoc rescue is NOT a
validation. Two standing discoveries: (1) **gateway deployments de-defer client tool schemas —
admission is REQUIRED in the gateway product just to reach parity with the native client**;
(2) the "1h" cache TTL is soft — N sailed through 65-min gaps with zero full misses, so ttl-gap
fires at ~65 min mutate a still-warm cache and are not free.

**v2 design deltas (all confound- or finding-driven):**
- T arm = `--disallowedTools` admission (the B6 list) + gated proxy — the real product
  configuration; kills the de-deferral confound (anchor: B6 measured 18,130 first-request tokens
  through this exact proxy+disallow config).
- Idle gaps dropped to 60 s (soft TTL makes 65-min gap-fires untrustworthy); the scheduler tests
  its cold-start + break-even rules only. The idle-gap lever returns to "modeled, pending a
  TTL-expiry measurement".
- Everything else unchanged: same 3 chained tasks, same grading, same endpoint definition.

**v2 preregistered prediction (frozen now, from the same calibrated machinery; T stream =
native timeline − measured 23,424/call admission delta, gated schedule):**

    T (admission + gated) vs N (native): BITE delta −29.5%, residency −46.3%, ~9 fires
    (cold-start + break-even), retirement/thinking contribution modest — decomposed via gw log.

    VALIDATION GATE: live pooled R$ ∈ [−36%, −22%]  → model validated in-regime
    live ∈ (−22%, −10%]  → direction confirmed, magnitude over-predicted; recalibrate
    live > −10%          → the admission+gated stack under-delivers live; investigate before
                            any product claim. (> 0% refutes outright.)

- **Budget: ≈$15–18 additional (B8 total ≈ $33; new hard cap $45, user-approved). 3 pairs,
  ~1 h/session, arms concurrent ⇒ ~3.5 h total.**
