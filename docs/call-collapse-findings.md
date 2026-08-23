# B5.1 — Discovery Call-Collapse Oracle: findings and the gate

**2026-08-23. Zero model quota. No executor built, no new tool surface, no live run.** Harness
`corpus/call_collapse_oracle.py`; frozen per-run artifact `corpus/analysis/call-collapse-oracle-v1.json`
(431 runs across 60 sessions, per-run records included). This is the measurement that decides whether
the ~50% stack is honest: the preregistered boundary was **≥15–20% safe call reduction ⇒ 50% credible**.

## Method (what makes this an oracle, not a guess)

- **Unit = one real API call** (requestId-merged; sidechain/subagent records excluded). Validated
  against CLI-reported `num_turns`: 56/58 sessions with CLI metadata match exactly (two off by 1–3,
  two lack the metadata).
- **Discovery is conservative**: Read/Grep/Glob or read-only Bash — and a read-only-looking command
  with a redirect/`tee`/`rm`-class side effect is *state-changing* (caught `cat > repro.py <<EOF`
  masquerading as `cat`). Runs never cross edits, tests, state-changing Bash, or no-tool calls.
- **Transitions classified mechanically** (no LLM judgment): **D0** = every target (path/pattern) of
  the next call appears verbatim in the *previous* call's output; **D1** = derivable from the run's
  *accumulated* inputs+outputs or bounded mechanical expansions (same-dir, test-file mapping,
  identifiers from outputs), or parameter-free read-only inspection; **D2** = not derivable — the
  model synthesized it. Spot-checked: D2 examples are genuine synthesis (`grep "class Col"`,
  `find -iname "*queries*"` — identifiers from reasoning, not from any prior output).
- **Evidence packet** = deduplicated union of the run's own outputs (per path the latest read; others
  hash-deduped). Nothing from the future.
- **Retention semantics**: a failure is evidence that was in the run's raw outputs but **lost in the
  packet**. Evidence the run never contained is *neutral* — it came from prior context, which a
  collapse leaves untouched — and is reported as provenance instead.
- **Savings are prefix-weighted with real per-call P_t**: a collapsed n-call run keeps its first call
  and avoids calls 2..n; saving = Σ P_t over exactly those calls. The packet adds no residency (it is
  the same outputs, deduplicated — dedup is a bonus, reported separately).

## Results (60 django sessions, 2,873 real API calls)

**Structure.** Discovery = 50.4% of calls; 431 runs, 238 multi-call; run length p50 = 2, p90 = 8,
p95 = 13, max = 32. Transitions: D1 733, D0 193, D2 140. Multi-call run classes: D0+D1 127, D2 95,
pure-D0 16.

**Call reduction (% of ALL real API calls):**

| estimate | calls | Σ P_t (prefix-weighted) |
|---|---:|---:|
| upper bound (every run → 1) | 35.9% | 31.3% |
| mechanical only (pure-D0 runs) | 0.6% | 0.6% |
| **realistic (D0+D1, evidence-gated)** | **13.1%** | **11.5%** |

**Evidence retention (the ≥95% requirement): PASSES.** R_next_action **97.1%**, R_edit_target
**100%**, R_edit_region **96.3%** (138 gateable runs; 75.4% of them actually sourced the next action's
evidence from the run — the rest drew on prior context, which collapse preserves).

**Executor feasibility signals**: choice breadth is small — median **2** candidate paths per
transition, and where the model picked from a listed candidate set it chose within the **top 3 in
94%** of cases; packet dedup saves 10.1% of raw output tokens; avoided assistant output ≈ 111k tokens
across the corpus.

## Against the preregistered gate

> <10% insufficient · 10–15% useful but likely just short · 15–20% credible-50 · >20% second primary lever

**Realistic safe collapse = 13.1% of all API calls → the 10–15% band: "useful but likely just short,
depending on environment."** Retention clears its 95% bar, so the number is *safe*; it is simply not
quite large enough on its own.

## The composed path-to-50 (multiplicative, measured values)

Inputs: prefix = doctor-v1 measured values; call-collapse = this measurement (prefix-weighted);
B3 = 8.3%; thinking-GC = a **range 0–7%** (mechanism live-validated, magnitude not yet measured by
workload — never substituted into the central claim).

| path | conservative (D0) | **central (D0+D1 gated)** | optimistic (upper bound) |
|---|---:|---:|---:|
| lean, subscription (prefix −31.9%) | 37.9 → 42.2% | **44.7 → 48.6%** | 57.1 → 60.1% |
| heavy, subscription (prefix −27.6%) | 34.0 → 38.6% | **41.2 → 45.3%** | 54.4 → 57.6% |
| lean, gateway (prefix −35.5%) | 41.2 → 45.3% | **47.6 → 51.3%** | 59.3 → 62.2% |
| heavy, gateway (prefix −45.3%) | 50.1 → 53.6% | **55.6 → 58.7%** | 65.5 → 67.9% |

*(each cell: without thinking → with the 7% thinking assumption)*

**Verdict (wording tightened per review, and superseded by the EXACT joint replay):** the measured
opportunity stack supports a **credible ~50% engineering target for the gateway architecture; live
end-to-end realization remains to be demonstrated.** The multiplicative table above was subsequently
shown to be 4–6pp too *pessimistic* — the levers remove absolute disjoint slices and compose
super-multiplicatively. The exact per-call joint replay (`docs/joint-stack-findings.md`) gives
counterfactual joint opportunities of **54.4% (lean-sub), 57.2% (lean-gw), 40.3% (heavy-sub), 53.9%
(heavy-gw)**, with per-session measured thinking instead of the assumed 7%.

## Caveats

- **The oracle replays the model's own trajectories.** Derivability (D0/D1) shows a local program
  *could* have fetched the same evidence; it does not prove the model, *given a packet*, behaves
  identically. That is what a scoped live A/B of an actual executor would test — deliberately not
  built here.
- A real executor must over-fetch to cover choice (median breadth 2, 94% top-3 — favorable, but
  packets will be somewhat larger than the replayed ideal).
- One task family (django SWE-bench, headless); interactive workloads have different run shapes.
- D1's "locally programmable" is a judgment encoded as lexical derivability + bounded expansions; the
  classifier is deliberately conservative (140 transitions stayed D2) but it is still a model of
  programmability, not a program.
- Thinking-GC enters the stack as a range, not a measurement; B3's 8.3% is corrected-turn-axis safe
  NET; prefix values are doctor-v1 counterfactuals (subscription values use only validated
  mechanisms).

## STOP

Per the directive: no executor, no new tool surface, no live quota. Deliverables frozen:
`corpus/analysis/call-collapse-oracle-v1.json`, this document, 7 deterministic tests, full suite green.
The decision this feeds: with call-collapse measured at 13.1% (safe), the 50% headline should be
attached to the **gateway product path**, and the subscription story stated as **~40–49%** — or the
executor experiment (converting D2 runs / validating packets live) becomes the next quota-spending
candidate.
