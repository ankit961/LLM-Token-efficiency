# G1 — Graph-First Context Compilation: offline findings + skeptical review

**Run 2026-08-18, ZERO Claude quota. No live experiment.** Offline replay over the 4 native
(A_native) Step-7 django trajectories + their per-task code graphs. `corpus/g1_replay.py`; raw
per-edit rows + provenance in `corpus/analysis/g1-results.json`. This is a go/no-go, and the honest
answer on this corpus is **NO-GO / inconclusive**, with an important reframing of *where* the
difficulty is.

## What was built (Steps 1–4, merged)

- `anchors.symbol_at(path, line)` — narrowest enclosing symbol (deterministic); traceback / file /
  path-scoped / free-text anchor resolvers (lexical = indexing, never model-visible).
- `compile.context_compile(...)` — resolve anchor → reuse the existing budgeted compiler
  (`read_symbol`→`build_bundle`→`render_symbol`), which **preserves the target body** (root
  downgraded last) — genuinely different from B2 skeletonization.

## Two regimes (the key methodological split)

The experiment separates the two halves the earlier work conflated:

- **Oracle** — anchor = the edited symbol itself. Isolates the COMPILER (an upper bound; NOT
  leakage-free). Answers: *given the right anchor, does graph compilation deliver the edit body +
  useful neighbors cheaply?*
- **Problem** — anchor resolved ONLY from the problem statement (no future leakage). The real
  end-to-end PIPELINE. Answers: *can we even find the anchor from what's available?*

Ablations per budget: **B** lexical (target impl, no neighborhood) · **C** graph (target +
neighborhood) · **D** skeleton (B2 `reduce_file` on the file). Budgets 512/1024/2048/4096.

## Results (14 scorable edits; 9 function/method, 5 module-level)

| metric (@budget 2048) | value |
|---|---:|
| **Oracle** edit-symbol recall (C) | 0.86 |
| **Oracle** edit-LINE recall (C, all edits) | 0.57 |
| **Oracle** edit-LINE recall (C, function-only) | **0.67** (mean line-coverage **0.78**) |
| **Oracle** edit-LINE recall (D skeleton) | **0.07** (coverage 0.26) |
| **Oracle** token compression (C vs native exploration) | **0.59** (bundle = 41% of native; 0.40 at B=4096) |
| **Oracle** graph increment over lexical (useful-recall C−B) | **+0.038** (edit-line B == C = 0.57) |
| **Problem** edit-symbol recall / root-is-edited | **0.00 / 0.00** |
| anchor signal in problem statements | traceback in **1/4** tasks; file:line in **0/4** |

## Against the preregistered go/no-go gate

| gate | result |
|---|---|
| edit-symbol recall ≥ 95% | **FAIL** — 0.86 even with an oracle anchor; 0.00 from the problem statement |
| edit-line/body recall ≥ 90–95% | **FAIL** — 0.57 (0.67 function-only) with an oracle anchor |
| bundle ≤ 50% (pref 30–40%) of native tokens | **PASS** — 41% at B=2048, 40% at B=4096 |
| C must beat B on a material axis | **WEAK** — edit-line B == C; useful-recall +0.038 only |

## The reframing (the actual finding)

1. **The bottleneck is ANCHOR RESOLUTION, not compilation.** Given a correct anchor, the compiler
   preserves function-edit bodies at 0.78 line-coverage and compresses ~2×, and it *crushes* the B2
   skeleton (0.78 vs 0.26 coverage) — so graph-source-compilation is decisively better than B2's
   body-dropping skeletonization. But resolving the anchor from a **prose** problem statement fails
   completely here (0/14 reach the edited symbol; only 1/4 tasks even has a traceback).
2. **The GRAPH adds little over lexical targeting (B ≈ C).** The graph *neighborhood* does not
   improve edit-line recall (the body is the root's, present in both) and lifts useful-symbol recall
   by only +0.038. Per the preregistered logic, *"if B ≈ C the graph adds little and the win is
   simply targeted source slicing."* That is what the data shows.

## Skeptical review (caveats that keep this from being a firm verdict)

- **Sample is tiny**: 4 django tasks, 14 scorable edits — suggestive, not decisive.
- **Metric limits, honestly**: edit-line recall is still partly understated (some `old_string`s span
  beyond one symbol, or differ by whitespace even after lstrip); useful-symbol recall has a noisy
  denominator (every symbol overlapping a read, including code the agent read but did not need);
  **5/14 edits are module-level (imports)** which symbol compilation inherently cannot target (the
  "symbol" is the 250-line module) — a real but narrow class.
- **Corpus lacks anchor signal**: django SWE-bench issues are prose. A bug corpus WITH tracebacks
  (many real bugs have them) could make anchor resolution far better — the pipeline failure is
  partly corpus-specific, not purely mechanism-specific.
- Token "compression" is a **counterfactual opportunity**, not a realized saving (no live run).

## Verdict and recommendation

**NO-GO for a live experiment now.** The gates fail even with an oracle anchor, and the free-text→
anchor half fails outright on this corpus. Do **not** run any live model experiment (Step 9 honored).

The result is not "the graph idea was tested at the wrong layer and it works." It is: **the compiler
half is sound and beats B2; the unsolved problems are (a) resolving an anchor from a real bug report
and (b) demonstrating that the graph neighborhood beats plain lexical target-slicing.** If pursued,
the next offline (still zero-quota) steps are: a stronger free-text→anchor resolver (or a
traceback-bearing corpus), a larger task set, and an ablation that gives the graph a fair chance to
beat B (e.g. tasks whose fix requires a caller/callee/test the target body alone does not contain).
Only if those move the B-vs-C and pipeline numbers materially would a live A/B be warranted.

The 0/11 advisory result stands for what it measured; this does not overturn it. Frozen B1 and the
B2 evidence artifacts were not touched.
