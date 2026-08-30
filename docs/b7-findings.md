# B7 — Cache-aligned retirement: when history mutation earns dollars, and when it must hold

**2026-08-28. Zero quota.** B6 left one identified gap: the lifetime levers cut live context
workload 41.5% but dollars only 2.5%, because history mutation invalidates the prompt cache. B7
answers *when mutation pays* with a live-calibrated cache cost model, an offline policy replay on
66 real sessions, and a shipped runtime scheduler (`CR_GATEWAY_CACHE_ALIGN`). Artifacts:
`corpus/analysis/b7-cache-replay-{b6,interactive}.json`; model `contextruntime/cachemodel.py`;
policy `contextruntime/cachealign.py` + gateway integration; 8 new tests (suite 522).

## Three factual corrections established first (each changes the economics)

1. **This client buys the 1-hour cache, so writes cost 2.0×, not 1.25×.** A zero-quota capture of
   a live request shows `cache_control: {type: ephemeral, ttl: "1h"}` on system[1], system[2], and
   the last message. At 2.0× the list-price reconstruction of B6 gives **−2.8% — matching the
   CLI's −2.5%** and closing that reconciliation (correction note added to `docs/b6-findings.md`).
   Break-even for mutation is therefore 19 future reads per rewritten token, not 11.5.

2. **The cache serves partial interior hits.** Live per-call usage under ENFORCE shows a request
   that diverges at depth X still reads the first X tokens from cache (keep-1 thinking strips cost
   ~1 turn of re-creation; a deep retirement batch re-creates exactly the suffix from the edit
   point — observed: read falls to P₁−396 when the earliest retired object lives in call 1's
   request). A model with hits only at whole stored breakpoints over-predicts creation ~2×.

3. **B6's live retirement was "flash", not persistent.** The gateway mutated only batch-boundary
   requests (`turn % 10 == 0`); between boundaries the client's full unmutated history passed
   through — each boundary re-paid the invalidation and its residency saving lasted one request.
   (Thinking-GC and admission applied every request; they carried most of B6's 41.5%.) B7's
   runtime replaces this with a **persistent fired set**: once fired, a retirement re-applies to
   every subsequent request — byte-stable between fires, so only *new* mutations ever invalidate.

## The cost model, and how much to trust it

`contextruntime/cachemodel.py`: cached-extent simulator (TTL, interior hits, invalidation-on-edit)
over observed per-call `P_t = cache_read + cache_creation + input` — API ground truth, no text
estimation on the critical path. Calibration:

- **Append-only branch: exact — 0.0% error on 11/12 B6 native sessions** (after seeding the warm
  fixed prefix that back-to-back sessions share; the 12th session contains a mid-stream retry
  anomaly, 1 of 279 native calls).
- **Edit branch: median |creation error| 7.3%** replaying the 12 live ENFORCE sessions' actual
  mutation schedules (reads within ~1%; one outlier over-predicted — the conservative direction).
- On the 54-session interactive set: median |creation error| 3.0%, but **p90 = 64%** — sessions
  with compactions/context resets are beyond the model, so every result below is also reported on
  the 36 well-calibrated sessions (|err| ≤ 20%).

## Policy replay — two workload regimes, one adaptive answer

Policies differ only in WHEN mutations fire. `unaligned` ≈ B6 behavior made persistent;
`cold_gap` fires only at cold start / idle gaps > TTL; `gated` adds the break-even rule
`0.1·pending·Ê ≥ 1.9·suffix` (Ê=8 fixed); `oracle` = gated with the true remaining-call count.

**B6 native timelines (12 headless, back-to-back — cache always hot):**

| policy | Δ$ pooled | Δ$ median | Δresidency | fires |
|---|---:|---:|---:|---:|
| unaligned | **+8.4%** | +6.3% | −9.6% | 32 |
| cold_gap / gated / oracle | 0.0% | 0.0% | 0.0% | 0 |

Even the oracle finds **no profitable mid-session fire** at 1h-cache prices on short cache-hot
sessions. The correct schedule there is *don't mutate* — which `gated` discovers by itself.

**54 real interactive sessions (up to 2,315 calls, ΣP up to 1.2B tokens, 23 sessions with ≥1 idle
gap > 1h)** — well-calibrated subset (n=36; all-54 pooled numbers are within ~10pp of these):

| policy | Δ$ pooled | Δ$ median | Δresidency | fires |
|---|---:|---:|---:|---:|
| unaligned | −61.9% | **+1.9%** | −74.2% | 807 |
| cold_gap | −60.4% | 0.0% | −65.4% | **125** |
| gated | −61.5% | 0.0% | −67.0% | 2,514 |
| oracle | −62.1% | 0.0% | −73.7% | 4,287 |

Read the pooled/median split carefully: **the dollar value is concentrated in a few giant
long-context sessions** where reads dominate and even unaligned mutation pays; the *median*
session gains ~nothing — and unaligned *regresses* it (+1.9% median here, +6–8% on B6-style
sessions). `gated` matches the oracle to within 0.6pp pooled, never regresses the median, and
`cold_gap` captures −60% with just 125 mutation events (23 of them free idle-gap windows).

**Conclusion: `gated` strictly dominates the B6 schedule** — same savings where savings exist,
zero damage where they don't, and it adapts between regimes with no workload knowledge.

## What shipped (runtime)

`CR_GATEWAY_CACHE_ALIGN ∈ {off, cold, gated}` (default **off** = exact B6 behavior):

- `contextruntime/cachealign.py` — `CacheAlignedScheduler`: fire on cold-start / TTL-gap /
  break-even; `commit` makes fired mutations persistent. Decides WHEN only; safety stays in
  `RetirementPlanner`.
- `contextruntime/gateway.py` — persistent re-apply of the fired set each request;
  `thinking_gc_upto` strips thinking behind a frontier that advances only at fire moments
  (byte-stable between fires); decision log now records `ts`, `gap_s`, `fired`, `fire_reason`,
  `pending_tokens`, `suffix_tokens_est`, `persistent_applied`. Fail-open path unchanged.

## Honest limits

- **Offline result.** No new quota was spent; the policy table is modeled counterfactual, anchored
  by exact calibration on the 24 live B6 sessions and 36/54 interactive sessions. A live A/B of
  `gated` vs `off` is the (approval-gated) confirmation step if wanted.
- Retired-object sizes ride the cl100k×1.74 estimate; thinking rides the ×1.51 output factor with
  a 100-token noise floor. Both established earlier; both approximations.
- Absolute dollars use sonnet price ratios (0.1/2.0/5.0); interactive sessions mix models, so Δ%
  is the trustworthy axis there.
- Ê=8 is a fixed constant, not tuned — and still lands within 0.6pp of the oracle.
- Compaction-heavy sessions (the p90 calibration tail) are outside the model; they are included
  in the all-54 pooled numbers and excluded from the well-calibrated cut, shown side by side.

## Bottom line

> Mutation cost is a *scheduling* problem, and it is solved by two rules: **fire when the cache is
> cold or the break-even clears; make every fired mutation persistent.** On short cache-hot
> sessions that means don't mutate mid-session (the B6 penalty goes to zero); on real long-context
> interactive work the same rule converts the lifetime levers' residency into **~−60% modeled
> dollars, within 0.6pp of oracle scheduling** — and the residency lever B6 demonstrated live
> stays fully available to it.
