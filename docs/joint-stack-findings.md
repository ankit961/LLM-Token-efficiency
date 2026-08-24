# B5.2 Stage A — Joint counterfactual replay (v1 provisional → v2 repaired)

> **Revision (2026-08-24, per review):** v1 below is NOT fully exact and is renamed **joint
> counterfactual replay v1 (provisional)**. Three repairs landed as **v2**
> (`corpus/analysis/joint-stack-replay-v2.json`): (1) the gateway prefix now replays **per-tool
> defer-at-first-use** (0,…,S from each tool's first use in that session; unused ⇒ deferred all
> session; scaled to each session's available prefix) instead of a constant fraction; (2) **B3
> retirement re-runs on the collapsed timeline** (objects/edits remapped, avoided-call objects live at
> the packet call, lag 5 in new-timeline calls); (3) **thinking-GC counts kept calls only** and uses an
> **independently measured OUTPUT-side factor 1.51** (p05–p10 lower envelope of out/vis over 219
> visible-heavy calls) — the input-side 1.74 accounting factor is no longer reused for output.
>
> | env | v1 (provisional) | **v2 (repaired)** |
> |---|---:|---:|
> | lean, subscription | 54.4% | **53.9%** |
> | lean, gateway | 57.2% | **58.2%** |
> | heavy, subscription | 40.3% | **40.2%** |
> | heavy, gateway | 53.9% | **59.0%** |
>
> Subscription numbers are ~unchanged (constant-H is correct for disable-class removal — v1 validated
> there); the repairs matter exactly where predicted: the **gateway** columns, whose advantage is
> deferral-at-first-use. All numbers remain counterfactual opportunities, not live savings.


**2026-08-23. Zero model quota.** Harness `corpus/joint_stack_replay.py`; frozen artifact
`corpus/analysis/joint-stack-replay-v1.json`. Replaces the multiplicative approximation with an exact
per-call replay so lever overlap is accounted for exactly, per review.

## Method

Every intervention is applied to the SAME per-call trajectory of each real session (requestId-merged,
sidechains excluded), cumulatively:

| level | intervention | source of the per-call value |
|---|---|---|
| L0 | baseline | real per-call `P_t` (cache_read + cache_creation + input) |
| L1 | prefix hygiene | `P_t − frac_env × startup_P` (doctor-v1 subscription/gateway fractions) |
| L2 | + D0/D1 collapse | drop the avoided calls of evidence-gated runs (oracle, per session) |
| L3 | + B3 retirement | `− Σ size` of SAFE objects retired before t (per-object verdicts, lag 5) |
| L4 | + thinking-GC | `− Σ think_s, s ≤ t−2` (keep-1), think **measured from this session** |

Per-call thinking is observed, not assumed: `think_t = max(output_t − 1.74 × cl100k(visible_t), 0)`
with the Claude request-accounting factor applied. (The earlier 11.3% thinking share **survives** the
factor correction — visible output is small; lean sessions carry ~130 thinking tokens/call.) The three
removed slices (schemas, retired outputs, retained thinking) are disjoint components of the prefix, so
per-call subtraction is exact; clamps at zero are counted (8 of 2,968 lean calls, 0 heavy).

A fixture test hand-computes every level; it caught (and fixed) a real off-by-one — the keep-1 rule
keeps the *latest* prior assistant message's thinking, so the strippable set at call t is calls 1..t−2.

## Results (counterfactual opportunities, NOT live realized savings)

| environment | L1 prefix | L2 +collapse | L3 +B3 | **L4 exact joint** | multiplicative | Δ (approx − exact) |
|---|---:|---:|---:|---:|---:|---:|
| lean, subscription (frac −47.0%) | 28.0% | 35.8% | 44.6% | **54.4%** | 48.6% | **−5.8pp** |
| lean, gateway (frac −52.4%) | 31.3% | 38.6% | 47.4% | **57.2%** | 50.9% | **−6.3pp** |
| heavy, subscription (frac −38.4%) | 22.0% | 24.0% | 33.1% | **40.3%** | 36.3% | **−4.0pp** |
| heavy, gateway (frac −63.0%) | 36.1% | 37.6% | 46.6% | **53.9%** | 47.8% | **−6.1pp** |

Solo levers (exact, same sessions): lean — prefix 28.0 / collapse 11.5 / B3 9.6 / thinking 10.8;
heavy — prefix 22.0–36.1 / collapse **2.8** / B3 9.2 / thinking 7.4.

## What changed vs the multiplicative model — and why

**The multiplicative approximation was 4.0–6.3 pp too PESSIMISTIC in every environment** (> the 2pp
update threshold). The reason is structural: prefix hygiene, B3 and thinking-GC remove **absolute,
disjoint token slices** from each remaining prefix, so they compose near-additively in tokens —
super-multiplicatively in fractions. The only true interaction losses are collapse × everything
(an avoided call cannot also be shrunk) and rare clamps, and they are outweighed.

Two further findings the exact replay surfaces:

- **Call-collapse is strongly workload-dependent**: 11.5% on headless django vs **2.8%** on this
  machine's interactive sessions (edits/tests interleave with discovery, so runs are short). The
  executor's value concentrates on autonomous/headless workloads.
- **Peak context falls too** (lean: mean peak 88k → 46k at L4) — the stack also defers native
  compaction, consistent with B3.2.

## Updated claims (wording per review)

- **The measured opportunity stack now supports a credible ~50% engineering target for the gateway
  architecture — and, on headless workloads, for the subscription path as well** (exact joint 54.4%
  lean-sub, 57.2% lean-gw, 53.9% heavy-gw; heavy-sub 40.3%). **Live end-to-end realization remains to
  be demonstrated.**
- Levels of evidence stay separated: prefix = measured + counterfactual replay (stripping mechanisms
  validated); collapse = offline oracle (retention-gated, executor not live-tested); B3 = offline safe
  estimate + small live safety test; thinking-GC = mechanism live-validated, magnitude here measured
  per session offline.
- `docs/path-to-50.md` and `docs/call-collapse-findings.md` carry this correction; exact joint
  supersedes the multiplicative arithmetic everywhere.

## Caveats

- All four numbers are **counterfactual opportunities** replayed over real trajectories; nothing here
  is a live A/B.
- Heavy environment n=8 single-window interactive sessions (this machine's own work) — a biased,
  small stratum; lean n=60 headless django.
- The thinking estimate inherits the accounting-factor uncertainty (measured ratio, not pure
  tokenizer); the factor-1.0 variant would raise L4 slightly (upper bound, not used).
- B3 modeled as immediate retirement (batching keeps ~90% of it); collapse assumes packets replace
  interleaved outputs at equal residency (dedup bonus ignored).

## v3 (2026-08-24, per review) — repairs + explicit conventions; PROVISIONAL, not exact

Four review findings repaired (`corpus/analysis/joint-stack-replay-v3.json`):

1. **Session-exact inventory**: the gateway defer schedule now uses each session's OWN resident tools
   (reference sizes minus that session's `deferred_tools_delta` names), capped — no foreign-inventory
   scaling.
2. **Packet-conservative B3 (central)**: a collapsed run's outputs become ONE object that retires at
   the max of its constituents' safe retire turns, only if all are safe (a real executor emits one
   combined tool result). The separately-addressable design is reported as a variant (~+0.5pp).
3. **Output factor**: 1.51 stays an envelope estimate; the L4 sensitivity band under 1.30–1.74 is
   ±0.7pp.
4. **Bookkeeping**: solo-thinking now uses the same output-factor path; L1 `sum_out` fixed.

**Explicit non-overlapping conventions**: subscription arms = constant doctor fraction; gateway arms =
**defer-only** (the schedule subsumes disallows; user-block compression omitted — conservative).

| env | v1 (provisional) | v2 | **v3 central** | factor band | addressable packets |
|---|---:|---:|---:|---|---:|
| lean, subscription | 54.4 | 53.9 | **53.4%** | — | — |
| lean, gateway | 57.2 | 58.2 | **56.0%** | 55.4–56.6 | 56.6 |
| heavy, subscription | 40.3 | 40.2 | **39.7%** | — | — |
| heavy, gateway | 53.9 | 59.0 | **54.7%** | 54.0–55.5 | 55.3 |

**Status: 39.7–56.0% PROVISIONAL COUNTERFACTUAL opportunity** — each repair moved numbers 1–4pp and
they now vary <1pp under the remaining known uncertainties (factor band, packet addressability). Still
not "exact": tool sizes come from a reference capture, the output factor is an envelope, and packet
residency assumes zero dedup bonus. Live end-to-end realization remains undemonstrated.
