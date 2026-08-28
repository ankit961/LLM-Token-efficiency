# B6 — Integrated Admission + Lifetime live A/B: preregistered protocol

> **Status update (2026-08-28): RUN COMPLETE on the subscription (user-approved, "use membership").
> 24/24 sessions; results and analysis in `docs/b6-findings.md`; frozen artifacts
> `corpus/analysis/b6-live-results.json` + `corpus/analysis/b6-gw-logs/`. One mid-run repair
> (grading test-file reset, `corpus/b6_grading.reset_test_files`) is documented there; the
> endpoints, gates, and thresholds below were not changed after the first paid run.**

**Original preregistration follows, frozen as written. No quota is spent until the budget line
below is explicitly approved.** This is the experiment that converts "~40–55% measured counterfactual opportunity" into a
live, graded, end-to-end number — or fails to. Everything here is frozen before the first paid run.

## Question

> Can the mechanisms that survived every ablation — prefix admission hygiene + B3 retirement +
> thinking-GC — produce a real end-to-end token reduction on successfully completed coding tasks?

Explicitly excluded from the treatment: `discover`, graph routing, search replacement, D2 — everything
closed by B5.

## Arms

- **N (native)**: stock `claude -p` on the task. No proxy, no flags.
- **T (treatment)**:
  - *Admission*: `--disallowedTools` with the never-used-in-headless schema list
    (`corpus/b6_live_ab.DISALLOW_ADMISSION`; validated: definitions are stripped from the request —
    the zero-quota chain dry-run measured **6 tools / 5.6k cl100k vs 82 tools / 46.1k baseline**).
  - *Lifetime*: the request path runs through the gateway proxy in **ENFORCE** —
    B3 safe retirement (superseded + cold-tail lag 5, batched every 10 calls) and thinking-GC keep-1 —
    with the response-level fail-open (any 4xx to a mutated body resends the original bytes).

Same model (sonnet), same prompts, same budget caps, fresh worktree per rep.

## Tasks and grading (real, no docker)

Modern SWE-bench-Verified django instances (2023+ base commits) whose grading is **proven on this
machine at zero quota** (`corpus/b6_grading.validate_task`: pre-fix FAIL_TO_PASS fails; gold patch
makes FAIL_TO_PASS pass and PASS_TO_PASS stay green, under `python3.11 tests/runtests.py`). Only
validated instances are eligible. The agent works on a tree WITHOUT the test patch; grading applies
`test_patch` to the edited tree afterwards and runs FAIL_TO_PASS + PASS_TO_PASS (P2P capped at 40
labels for runtime). **A rep is a SUCCESS only if F2P passes and P2P stays green.**

## Replication and budget

- **4 tasks × 3 paired reps × 2 arms = 24 sessions** (reps interleaved N,T,N,T…), `--max-budget-usd
  2.5` per session, 900 s wall cap.
- **Expected spend ≈ $40–55; hard cap $60.** If the cap is reached mid-run, completed pairs stand
  (incremental saves); no partial pair is analyzed.
- Same-task rep variance was measured at up to 1.9× (Step 7) — this is why reps, not more tasks.

## Endpoints (preregistered)

Primary:

    R = Σ input_T / Σ input_N        (per task, paired reps; and pooled)
    where Σ input = Σ per-call (cache_read + cache_creation + input) from the transcript
    (requestId-merged, sidechains excluded — the same authoritative source as every replay)

Secondary: cache-read, cache-creation, output tokens, dollar cost (CLI-reported), real API calls,
peak per-call context, compaction events (expected 0 — single-window), gateway actions
(tool-results retired, thinking blocks stripped, fallback_original count), and **GC-caused re-reads**
(Reads of a path after the gateway retired that path's object).

Success/quality: graded task success per rep; **treatment success must be non-inferior** (successes_T
≥ successes_N − 1 across the 12 treatment reps, and no task where T fails all reps while N passes all).

## Decision thresholds (the user's, verbatim)

    live reduction 20–30%  → useful product
    live reduction 35–45%  → very strong result
    live reduction 45–55%  → the original ~50% goal is essentially achieved

Reference: the no-collapse counterfactual for this configuration (lean-ish headless + gateway) is
**~52%**; the subscription-only portion (admission alone) is ~28–47% depending on environment.

## Honesty rules

- Report R with per-task values and the pooled value; never only the most favorable.
- A timed-out or budget-capped rep is reported as such; its pair is excluded from R but counted in the
  quality table.
- Any gateway `fallback_original` events (mutation rejected upstream) are reported; >2 per session
  triggers a mechanism review before continuing.
- The treatment's own gateway logs are frozen with the artifact
  (`corpus/analysis/b6-live-results.json` + per-rep `.gw.jsonl`).

## Zero-quota readiness (done before any spend)

1. ✅ Grading proven per instance against gold patches (label-format fixes included: new-format ids and
   docstring-named tests).
2. ✅ Treatment chain dry-run: `claude -p` → enforce-proxy → capture upstream — disallowed schemas
   absent (82→6 tools), thinking config intact, gateway decisions logged, zero tokens to Anthropic.
3. ✅ Runner (`corpus/b6_live_ab.py`): fresh worktree per rep, per-arm proxy lifecycle, incremental
   saves, resume, timeout-tolerant, grading wired in.

**STOP: awaiting explicit budget approval (≈$40–55, cap $60) and any replication adjustments.**
