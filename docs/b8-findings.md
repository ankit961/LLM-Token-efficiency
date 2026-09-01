# B8 — Live validation of the cache-aware gateway stack: **−29.3% live dollars, predicted −29.5%**

**2026-08-28/29. Live, on the subscription. Two runs: v1 (confounded, $16.65) and v2 (clean,
$11.19) — B8 total $27.84-equivalent against the $45 cap.** Protocol and both preregistered
bands frozen before their respective first paid sessions (`docs/b8-protocol.md`). Artifacts:
`corpus/analysis/b8v1-live-results.json`, `b8v2-live-results-{N,T}.json`,
`b8-gw-logs/b8v2-T*.gw.jsonl`, configs alongside.

## The v2 result (the clean experiment)

3 pairs; each session = three chained graded django tasks in one conversation (~120k context by
task 3); **N** = native, **T** = `--disallowedTools` admission + gateway ENFORCE with
`CR_GATEWAY_CACHE_ALIGN=gated` (persistent fired set; cold-start/break-even firing; 60s
inter-task pauses — no TTL gaps, see v1 finding 2).

| pair | N BITE | T BITE | R$ | Δ |
|---|---:|---:|---:|---:|
| 0 | 894,926 | 617,611 | 0.690 | −31.0% |
| 1 | 551,222 | 518,781 | 0.941 | −5.9% |
| 2 | 737,518 | 406,481 | 0.551 | −44.9% |
| **pooled** | **2,183,666** | **1,542,873** | **0.7066** | **−29.34%** |

- **Preregistered prediction: −29.5%; gate [−36%, −22%]. Live: −29.34% — validated to 0.2pp.**
- **The CLI's own cost report independently agrees: −29.2%** ($6.55 N vs $4.64 T) — the
  list-price BITE accounting (read 0.1 / 1h-write 2.0 / output 5.0) prices real sessions
  correctly.
- **Quality: 9/9 vs 9/9 graded task-instances** — perfect in both arms.
- Residency Σ P: −40.1% (predicted −46.3%).
- Mechanism: 0 `fallback_original` in 3/3 T-sessions (now 17/17 live ENFORCE sessions across
  B6+B8 without a single rejected mutation). Per-pair spread (−5.9% to −44.9%) is the familiar
  same-task rep variance; the pooled ratio is the preregistered endpoint.

**Decomposition, honestly:** the live saving is essentially **all admission**. The gated
scheduler fired only at cold-start; the break-even (`0.1·pending·8 ≥ 1.9·suffix`) never cleared
in these ~120k-context sessions, so retirement and thinking-GC contributed zero — and *that is
the scheduler working as designed*: at 1h-cache write prices, mid-session mutation on sessions
this size is not profitable, and the scheduler declined it while costing nothing (its no-harm
property is exactly what B6's unaligned schedule lacked). The model had predicted 9 marginal
break-even fires contributing a few thousand tokens; live produced none — a knife-edge
miscalibration on a component whose predicted contribution was already minor. The residency
shortfall (−40.1 vs −46.3) is this same component.

## What v1 bought with its $16.65 (confounded, but two lasting discoveries)

v1 (T = gated proxy *without* admission, real 65-min idle gaps) blew its band (+139.9%/+35.5%)
for a reason that had nothing to do with the scheduler:

1. **A custom `ANTHROPIC_BASE_URL` makes the client disable MCP tool-schema deferral** — the T
   arm carried all ~82 schemas (first request 84,676 tokens vs native-deferred 41,554) plus six
   read=0 full-prefix rewrites on tool-list changes (one on a request with zero gateway mutations
   active). Post-hoc removal of that mass lands T0 at ≈ −16%, inside v1's band — but per
   preregistration rules v1 stays scored as invalid-as-a-test. **Product consequence: admission
   is not optional in a gateway deployment; it is required just to reach parity with the native
   client.** v2's design (admission in the treatment) is the product configuration, and it
   validated.
2. **The "1h" cache TTL is soft**: the native arm sailed through both 65-minute idle gaps with
   zero full misses. A ttl-gap fire at ~65 min therefore mutates a still-warm cache and is not
   free — the idle-gap lever returns to "modeled, pending an empirical TTL-expiry measurement."
3. v1 quality was also perfect (12/12 graded successes) and the scheduler mechanics were correct
   throughout (monotone persistent set, fires only at designed moments, 0 fallbacks).

Operational note: the v1 scratchpad (gateway logs, configs, mirror) was lost to a /tmp purge
between sessions; the session transcripts (the authoritative usage source, in `~/.claude`)
survive, v1 numbers were recomputed from them bit-identical to the in-run analysis, and the
gw-log aggregates extracted during the run are recorded in the frozen artifact.

## Where this leaves the program's claims

| claim | status after B8 |
|---|---|
| Cache cost model (BITE accounting, extent semantics) | **Live-validated**: 0.2pp on a preregistered prediction; CLI cost agrees to 0.1pp |
| Admission as the dollar lever | **Live-demonstrated: −29.3%** on chained multi-task sessions (on top of B6's −41.5% workload result) |
| Gated scheduler no-harm | **Live-demonstrated**: fired nothing unprofitable, cost nothing, 0 fallbacks |
| Retirement/thinking-GC as *dollar* levers at 1h prices | Still **modeled-only** — profitable only in the giant long-context regime (B7's tail); B8's ~120k sessions never reach break-even, and the scheduler correctly holds |
| Idle-gap free windows | Weakened: TTL is soft at 65 min; needs an expiry measurement before use |

The remaining unvalidated number is unchanged in kind but sharpened in scope: **B7's ~−60%
pooled interactive counterfactual now rests on a model that has survived a live preregistered
test in its holding regime and in admission pricing — but its firing-regime payoff (giant
sessions, real multi-hour gaps) has still never been demonstrated live.**
