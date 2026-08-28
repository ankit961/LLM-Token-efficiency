# B6 — Integrated Admission + Lifetime live A/B: **41.5% live end-to-end input reduction**

**2026-08-27/28. Live, on the subscription (no API key on the machine; the CLI's own OAuth is
relayed by the proxy and never stored). 24/24 sessions completed — no timeouts, no budget caps.
Spend: $15.76-equivalent of plan usage ($7.98 N + $7.78 T) against the $60 ceiling.**
Protocol preregistered in `docs/b6-protocol.md` before the first paid run; frozen artifacts:
`corpus/analysis/b6-live-results.json` + `corpus/analysis/b6-gw-logs/*.gw.jsonl` (per-session
gateway decision logs). Arms: **N** = stock `claude -p` (sonnet); **T** = admission
(`--disallowedTools`, 82→6 tool schemas in the request) + gateway ENFORCE (B3 safe retirement +
thinking-GC keep-1) — no discover, no graph, no search replacement.

## Primary endpoint (preregistered): R = Σ input_T / Σ input_N

Σ input = per-call (cache_read + cache_creation + input), transcript-derived (requestId-merged,
sidechains excluded). No rep excluded (exclusion criteria — timeout/budget-cap — never triggered).

| task | Σ input N | Σ input T | R | reduction | success N | success T |
|---|---:|---:|---:|---:|:--:|:--:|
| django-16485 | 2,772,225 | 1,682,341 | 0.607 | **−39.3%** | 3/3 | 3/3 |
| django-16502 | 4,753,322 | 3,418,567 | 0.719 | **−28.1%** | 2/3 | 0/3 |
| django-16527 | 3,606,230 | 2,055,051 | 0.570 | **−43.0%** | 3/3 | 3/3 |
| django-16901 | 3,734,336 | 1,543,827 | 0.413 | **−58.7%** | 2/3 | 3/3 |
| **pooled** | **14,866,113** | **8,699,786** | **0.585** | **−41.5%** | **10/12** | **9/12** |

Per-pair reductions (interleaved N,T reps): 28.1, 22.1, 56.8 | 38.6, −68.1†, 53.6 | 48.5, 35.3,
45.3 | 79.8, 65.0, −134.8†% (†pairs where the N rep was unusually short/failed; the task-level Σ
absorbs them — per-pair spread is exactly the 1.9× same-task variance Step 7 measured).

**Against the preregistered thresholds: 41.5% falls in the 35–45% band — "very strong result."**
It does not reach the 45–55% ("original ~50% goal essentially achieved") band.

## Quality gate (preregistered): PASS, on the bound

- successes_T = 9 ≥ successes_N − 1 = 9 ✅ (non-inferiority, exactly at the bound)
- No task with T 0/3 while N 3/3 ✅ (16502 is T 0/3 but N 2/3)

**16502 examined, not waved away.** All four failures on the task (1 N + 3 T) are the identical
mode: same file (`django/core/servers/basehttp.py`), exactly one FAIL_TO_PASS assertion failing,
PASS_TO_PASS green. The task has a *shallow* fix (suppress the response body for HEAD requests)
that fails one assertion and a *deep* fix (also strip `Content-Length` and override
`finish_response`) that passes. N1's failing fix and T1's failing fix are essentially **the same
patch** — the failure mode exists identically in the native arm. N found the deep fix 2/3, T 0/3;
under exchangeability the probability of that split is ≈0.2 (hypergeometric) — consistent with
chance, and n=3/arm cannot rule out a treatment effect either. Recorded as the honest residual
risk of this experiment. The 16901 counter-example ran the other way: N2 concluded after 7 calls
with a non-fix (an import line and a self-written test, no logic change) while T went 3/3.

## Mechanism health: perfect across all 12 treatment sessions

- **fallback_original = 0 in 12/12 sessions** — every mutated request body was accepted by the
  API. 353 tool results retired, 1,794 thinking blocks stripped (gateway decision logs frozen).
- **No retirement-caused re-reading**: repeat-Reads of the same path are N 11/36 vs T 12/39 —
  indistinguishable. B3.3's "no re-read tax" now holds at 12-session scale.
- Real grading (F2P + P2P run natively per rep) — process-completion was never used as success.

## Secondary endpoints — including the one that cuts against us

| metric (Σ over 12 reps) | N | T | Δ |
|---|---:|---:|---:|
| API calls | 279 | 311 | **+11.5% more** |
| cache_read | 14,478,012 | 8,047,610 | −44.4% |
| cache_creation | 387,545 | 651,554 | **+68.1% more** |
| output | 85,274 | 93,677 | +9.9% more |
| CLI-reported cost | $7.98 | $7.78 | −2.5% |

**Input-token residency fell 41.5%; dollar cost fell only 2.5%.** Retirement and thinking-GC edit
the conversation prefix, which invalidates the incremental prompt cache: T converts cheap
cache-read tokens (0.1× price) into expensive cache-creation tokens (1.25×), and the pricing
asymmetry consumes almost the entire saving at current API rates. T sessions also ran slightly
more calls (+11.5%, ordinary iteration variance by the re-read analysis) and slightly more output.
Under *this* billing model, B6's treatment is roughly **cost-neutral** while cutting the context
workload nearly in half.

What the 41.5% is therefore worth depends on what is scarce:

- **Context-window residency / capacity** (the program's Σ P_t target from B1 onward): −41.5%,
  live and graded. Peak per-call context fell in every task (e.g. 16485: 57k→34k peak P_t) —
  headroom before compaction roughly doubles, per B3.2's deferral finding.
- **Provider-side compute / weighted throughput** (what rate limits and plan quotas track):
  between the two, closer to the residency number than the dollar number.
- **API dollars at today's cache pricing**: ≈0% for the lifetime levers. Admission alone (no
  prefix edits after turn 1, so cache-friendly) is the dollar-saving component; a
  cache-aware retirement policy (retire only at natural cache boundaries, batch edits with
  checkpoint alignment) is the obvious engineering path to recover dollars — future work, out of
  scope for this cycle.

## Reconciliation with the Stage-A counterfactual

The v3 no-collapse counterfactual band for this program was 39.4–54.9% (environment-dependent).
The B6 environment is the heavy-MCP machine with a hybrid treatment (subscription-style admission
+ gateway lifetime): live **41.5%** lands inside the modeled band, nearer its heavy-subscription
edge (39.4%) than the full-gateway edge (54.9%) — the gap is the model's conservatism gradient:
live sessions vary their trajectories (the counterfactual replays fixed ones), and admission here
used `--disallowedTools` rather than gateway prefix replacement, leaving the residual system
prompt intact.

## Honest accounting

- One grading defect found live and fixed mid-run at the cost of one interrupted session setup:
  agents add their own regression tests to the very files the official `test_patch` touches,
  which made the patch fail to apply (validation applied it to clean trees and could not see
  this). Fix follows the SWE-bench convention: official test files are reset to base before
  `test_patch` (`corpus/b6_grading.reset_test_files`; regression-tested). The first pair's edited
  trees were reconstructed exactly by replaying their Edit/Write calls from the transcripts
  (verified: no file-mutating Bash in either session) and re-graded — both turned out to be
  successes. Marked `salvaged_grading` in the artifact.
- 9/12 N reps and 9/12 T reps had their official-test-file edits reset before grading — exactly
  symmetric across arms (the convention treats those edits as grading-irrelevant in both).
- Model = sonnet; single machine; 4 django tasks; n=3 pairs/task. Same-task variance is large
  (per-pair spread above), which is why only Σ-based task-level and pooled R are decision-grade.
- The subscription cannot express gateway admission (prefix replacement): T is the
  best *deployable-today* hybrid, not the maximal gateway configuration Stage A modeled.

## Bottom line

> The mechanisms that survived every ablation — admission hygiene + B3 retirement + thinking-GC —
> were run **together, live, end-to-end, with real task grading**, for the first time.
> **Live pooled input reduction: 41.5% (R = 0.585), with task success non-inferior (9 vs 10 of
> 12) and zero mechanism failures.** The program's "credible ~50% engineering target" is now a
> **demonstrated ~40% live saving** on context workload — with the honest caveat that current API
> cache pricing converts almost none of the lifetime-lever saving into dollars; admission is the
> dollar lever, and cache-aligned retirement is the identified path to close that gap.
