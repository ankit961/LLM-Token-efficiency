# ContextRuntime research papers

This directory turns the repository's frozen measurement and B-series evidence into two complementary research papers.

| Paper | Central question | Strongest supported claim | PDF | Source |
|---|---|---|---|---|
| **Tokens Are Multiplied by Turns** | Where does coding-agent context accumulate, and which parts are prospectively reducible? | Fixed context dominates the measured prefix; conservative and broad read-reduction opportunity ceilings are 20.9% and 48.5% of raw read tokens on the 30 token-share-eligible runs (21.3% and 51.0% on all 50), but several intuitive retrieval/compression interventions fail end-to-end. | [`dist/context-residency-measurement.pdf`](dist/context-residency-measurement.pdf) | [`measurement/main.tex`](measurement/main.tex) |
| **ContextRuntime** | Can a runtime reduce token residency and API cost without sacrificing coding outcomes? | The treatment arm (admission plus gateway) reduces pooled live input 41.5% but client-reported cost only 2.5%, and admission alone is predicted to account for the input reduction. In B8 v2, admission (a client launch flag; the gateway applied no mutation) cuts live list-price cost 29.34% with 9/9 success in both arms; a 29.5% model figure agrees, but it was committed with the results, so it is a post-hoc check. | [`dist/contextruntime-systems.pdf`](dist/contextruntime-systems.pdf) | [`runtime/main.tex`](runtime/main.tex) |

The papers deliberately separate four evidence grades:

- **Live:** observed in API-backed runs graded by executing tests (a local test runner for B6 and B8, not the official SWE-bench harness).
- **Modeled:** replayed through a calibrated cache-cost model, not claimed as a live result.
- **Retrospective:** an opportunity ceiling labeled after a trace completed, not a deployable policy.
- **Negative:** an attempted mechanism that failed its gate or increased end-to-end cost.

The companion files are:

- [`RESEARCH_LANDSCAPE.md`](RESEARCH_LANDSCAPE.md) — what prior researchers have done, what each line optimizes, and the remaining gap.
- [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) — frozen inputs, regeneration commands, checks, and claim provenance.
- [`references.bib`](references.bib) — shared bibliography.
- [`scripts/generate_assets.py`](scripts/generate_assets.py) — recomputes statistics, tables, and figures from committed JSON artifacts.

## Narrative across the two papers

The measurement paper establishes the denominator: an admitted token is charged repeatedly over its remaining turns, so locally impressive compression can be irrelevant—or harmful—at session scale. It then uses failed interventions to narrow the design space. The systems paper begins where that evidence ends: prevent large fixed objects from entering, retire safe history objects only prospectively, and schedule mutation around cache economics. The cleanest systems result is also the most instructive one: in the B8 live cost experiment the gateway applied no mutation—in that version its break-even branch could not fire on the Anthropic cache profiles (corrected afterwards, not yet run live)—so all savings came from admission control, a client launch flag that has not yet been compared with the client's own tool search.

## Build

From the repository root:

```bash
make -C papers all
```

This regenerates all numbers and figures before compiling both PDFs. See [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) for the full verification sequence.

## Scope

The live evidence is from one client/provider path, one model, one client version, one machine, and Django/SWE-bench-style tasks (four in B6, three in B8). The B6 quality check is a frozen operational gate, not a powered statistical proof of non-inferiority, and B8's tasks exclude the one task where B6 treatment failed. The giant-session result (61.5% on a calibration-filtered subset; 49.7% over all 54 replayed sessions) is modeled, comes from 8 interactive sessions, and uses a replay rule that the shipped scheduler does not execute. These limitations are part of the claims, not footnotes to them.
