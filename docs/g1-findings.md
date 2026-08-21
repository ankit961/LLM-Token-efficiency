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

## The fair B-vs-C ablation (does the graph help on MULTI-SYMBOL fixes?)

B==C on edit-line because both render the target *body*; the graph's real value proposition is
assembling the OTHER symbols a fix touches. So the fair test (`fair_bc_ablation`,
`corpus/analysis/g1-fair-bc.json`): for every ordered pair of co-edited symbols (root a, other b),
can the graph surface b from a? B (target-only) surfaces **zero** co-edits by construction, so C's
entire advantage is bounded by whether b is graph-adjacent to a **at all** — given the graph its
fairest chance (callees, callers, tests, AND same-file, up to 3 hops).

Over 42 co-edited pairs (from the multi-symbol tasks):

| co-edit relation to the anchor | share |
|---|---:|
| none (no graph edge, not same file) | **28/42 = 67%** |
| same_file | 8/42 = 19% |
| caller | 3/42 = 7% |
| callee | 3/42 = 7% |

- Via actual dependency edges, only **14%** of ordered co-edit pairs are linked; crediting same-file
  raises it to **33%**; **67% have no tested graph relation at all.**

**What this does and does NOT establish (four corrections to an earlier, over-reaching draft):**

1. **This is not a ceiling on graph-assisted context retrieval — only on co-edited-symbol
   PREDICTION.** Reachability between two *edited* symbols ≠ useful-context recall. A graph can still
   save tokens by surfacing symbols that are READ but never edited — a caller, a type/definition, a
   test, a validation helper the agent needs to modify the target correctly. "33% is C's ceiling over
   B" bounds only "can this structural graph predict the OTHER edited symbol," not "tokens avoided via
   support-context retrieval." That is the wrong (too narrow) value function; G2 measures the right one.
2. **The 33% over-credits today's compiler.** C's actual bundle planner traverses only
   `CALLS/IMPLEMENTS/IMPORTS`; the fair ablation additionally credited reverse callers, `DEPENDS_ON`,
   tests, and same-file. So 33% is an **upper bound for an ENRICHED structural neighborhood**, not
   what the shipped bundle achieves — a reason to *enrich* the graph, not to close it.
3. **Missing edges bias AGAINST the graph.** If extraction misses a real edge, that pair is
   classified `none` — so incomplete extraction **inflates** the 67% `none`. Better extraction can
   only *lower* it. The measured 67% is therefore an *upper bound* on disconnection, not a robust
   floor. (My earlier draft had this backwards.)
4. **n ≈ 2, not 42.** All 42 ordered pairs come from just **two** multi-symbol tasks (12 + 30; the
   other two contribute zero), and pairs within one fix are strongly dependent. Report "28/42 pairs"
   descriptively; do **not** treat 67% as a stable corpus-level property of bug fixes.

**The valid, valuable result:** *plain dependency structure is insufficient to explain many
multi-symbol fixes* — on these two Django cases only 14% of co-edit pairs are dependency-linked,
another 19% merely same-file. That means **dependency graph ≠ task-relevance graph**, not "graphs are
useless." The sharper hypothesis this leaves: a *task-relevance* graph (structural + locality +
behavioral + historical + semantic relations) might reduce exploration tokens where a plain
code-dependency graph cannot. **G2** (`docs/g2-*`) tests exactly that, offline, before any live run.

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

The result is not "the graph idea was tested at the wrong layer and it works" — nor is it "the graph
is useless." It is: **the compiler half is sound and beats B2; the current STRUCTURAL
(`CALLS/IMPORTS/IMPLEMENTS`) dependency graph is insufficient to explain many multi-symbol fixes; and
free-text anchor resolution fails on prose.** The fair ablation does NOT settle whether a graph can
retrieve useful support-context — it bounds only whether *this* structural graph can predict the
*other edited* symbol (33% ceiling, an enriched-neighborhood upper bound), a narrower question.

**The sharper hypothesis this leaves — and the right next test — is G2 (task-relevance graph
ceiling).** Before sourcing a new (traceback-bearing) corpus, which primarily repairs *anchor
resolution*, the deeper question is: *even with a correct anchor, does a cheap, deterministic
task-relevance graph contain the useful neighborhood the agent actually reads?* G2 answers that on
data we already have, at zero quota, with a hard preregistered gate:
`R_task − R_lexical < 0.15 at comparable-or-lower token cost ⇒ close graph traversal as a
token-saving mechanism`. **No live experiment is warranted until G2 reports.**

The 0/11 advisory result stands for what it measured; this does not overturn it. Frozen B1 and the
B2 evidence artifacts were not touched.
