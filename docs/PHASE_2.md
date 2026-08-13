# Phase 2 — SemanticFS + Graph-Lite (in progress)

Phase 2 is the **first real product gate**. This increment lands the foundation:
the **CodeSymbol graph** (Graph-Lite, C1) with language adapters and per-edge
confidence/provenance. Later increments add the budgeted bundle generator, read
classification, the MCP read surface, and the A/B/C/D experiment — see
[STATUS.md](STATUS.md) for the full plan and gates.

## What's in this increment

### Implemented today (accurate capability list)

| Piece | Status |
|---|---|
| `CodeSymbol` schema + `symbols`/`code_edges` tables (repo-scoped, idempotent re-index) | ✅ |
| Python adapter via **stdlib `ast`** — exact structure, calls, imports, inheritance, tests | ✅ high-confidence structural parsing |
| **tree-sitter** adapter — structure + scope-qualified names + recursive calls + imports (JS/TS/Go/Java/Rust when the grammar is installed) | ✅ structural; relationship coverage language-dependent |
| Regex **heuristic** fallback — structure only (no CALLS; attribution would be a guess) | ✅ low confidence |
| **Package-qualified module identity** (`payments/utils.py` ≠ `users/utils.py`) | ✅ (Phase 2.1) |
| **Scope-aware resolver with explicit ambiguity** — never `candidates[0]` | ✅ (Phase 2.1) |
| Edges `CONTAINS · IMPORTS · CALLS · IMPLEMENTS · TESTED_BY · DEPENDS_ON`, each with `confidence` + `resolution` + `match_kind` | ✅ |
| `REFERENCES` | 🔒 schema-reserved (not yet emitted) |
| import-aware resolution (`from x import foo as bar`) | ▶ next resolver iteration |
| **Budgeted bundle generator** (`build_bundle`, symbol × representation-level) | ✅ (Phase 2.2) |
| **Representation materializer** (L0 id … L4 impl, content-monotone) — planner → compiler | ✅ (Phase 2.3) |
| **Rendered budget validator** (validates *rendered* tokens ≤ B, not just the estimate; reports PRE) | ✅ (Phase 2.3) |
| `context_search` · `read_symbol` · `read_slice` · `find_callers` · `context_expand` (read surface) | ✅ (Phase 2.3) |
| **Serialized-budget enforcement + PRE hygiene + `safety_margin` + materialization-quality honesty** | ✅ (Phase 2.3.1) |
| **Python call-scoping parity** (nested-function calls attributed correctly, like tree-sitter) | ✅ (Phase 2.3.1) |
| Read classification (exploration vs edit_precondition …) | ▶ next |
| Empirical precision/recall (Gate-2A ground truth) · A/B/C/D harness | ▶ next |

`IMPORTS`/`IMPLEMENTS`/`TESTED_BY` are **adapter-dependent** (full in Python; structural
in tree-sitter; partial in the heuristic). "tree-sitter parsed the code" means
high-confidence *structural parsing* — not that the cross-language dependency graph is
complete or its resolutions verified.

## The confidence model (design C3)

Every edge carries a `confidence` and a `resolution` provenance, so a dependency
bundle never pretends all languages have equally sound analysis:

| resolution | used by | typical confidence |
|---|---|---|
| `python_ast` | Python (stdlib) | CONTAINS/IMPORTS ~0.95–1.0, CALLS ~0.75 |
| `tree_sitter` | JS/TS/Go/Java/Rust (grammar installed) | CONTAINS ~0.9, CALLS ~0.7 |
| `regex_heuristic` | fallback | ~0.5–0.7 |
| `derived` | `DEPENDS_ON` rolled up from resolved edges | inherits source |

**Structural certainty > call resolution** is the cross-language invariant: containment
is near-certain; call/reference resolution is where language dynamism bites, so it scores
lower — and lower still for dynamic languages.

**Confidence is an assigned prior, NOT measured quality.** `python conf=0.66` does not mean
66% of edges are correct; empirical precision/recall come from the Gate-2A ground-truth
dataset. The report is titled *Structural Confidence Report* to keep that honest.

### Resolution `match_kind` (Phase 2.1 — the safety ladder)

Every resolved edge records how sure the resolution is, so the bundle generator can trust
some edges and refuse to invent dependencies from others:

| match_kind | meaning | bundle use |
|---|---|---|
| `exact` | dst is a full qualified name | **HARD** — may be mandatory |
| `scoped` | resolved in the src's class/module scope (`self.method`, same-module) | **HARD** — may be mandatory |
| `inferred` | exactly one repo-wide symbol has that short name | **SOFT** — a single-candidate guess (`props.soft=true`); never mandatory |
| `ambiguous` | **multiple candidates — resolver refuses to pick one** (`ambiguous:<name>`, `ambiguity_count`) | no dependency; may show candidate signatures |
| `unresolved` | external/dynamic — no repo symbol | no dependency |

`DEPENDS_ON` is derived only from `exact`/`scoped` (**hard**) and `inferred` (**soft**),
never from `ambiguous`/`unresolved`. `inferred` is a *guess* — the receiver may be
external (`callback.save()` where exactly one `save` exists locally) — so its `DEPENDS_ON`
carries `props.soft=true`, and the bundle generator must never put a soft dependency in its
mandatory set. Two verified properties: (1) `save()` with two candidates is `ambiguous`,
not resolved to whichever was indexed first; (2) a uniquely-named cross-module call is
`inferred`+soft, not silently promoted to a hard dependency.

## Run it

```bash
python3 -m contextruntime.cli index-code path/to/repo --db graph.db
# Python needs no extra deps. For JS/TS/Go/Java/Rust at high confidence:
pip install -e ".[codegraph]"
```

Example (this repo's package + a JS sample):

```
Structural Confidence Report — repo 'sample'
  symbols : {'javascript': 4, 'python': 7}
  parsers : {'javascript': 'tree_sitter', 'python': 'python_ast'}
  resolution match kinds: exact / scoped / inferred / ambiguous / unresolved
  structural confidence by language (assigned prior):
    javascript   conf=0.90  parser=tree_sitter
    python       conf=0.78  parser=python_ast
```

## Budgeted bundle planner (Phase 2.2 / 2.2.1)

`build_bundle(store, root_symbol_id, budget, max_depth, policy) -> Bundle` — the first
component that directly attacks source-token admission. It chooses a small graph
projection that is still sufficient, selecting **both which symbols and at what
representation level** (a 40-token signature can beat a 900-token implementation).

It is a **planner, not yet a compiler**: token costs are estimates from stored metadata
(line span, signature length). Phase 2.3 adds the representation **materializer** that
renders actual source-derived text and validates the *rendered* token budget.

```
objective:  max Σ U(v, level)  s.t.  Σ tokens(v, level) ≤ B,  one level per symbol
            U = R · C · W · D · F   (structural only — no NL reranker yet)
```

Solved by a **deterministic monotone greedy approximation** — not an exact `argmax`:
**mandatory = root only** (hard deps are *eligible*-for-mandatory, not auto-mandatory — a
true required core needs `TYPE_USES`/signature-dependency data we don't extract yet), then
one budget-independent increment order over a per-symbol **cost ladder** (equal-cost levels
collapsed, so tiny symbols aren't stuck at signature), applying the longest fitting prefix.

Guaranteed properties (all tested):

- **budget invariant** `used ≤ B`; exact boundary, no off-by-one
- **root preservation**, or explicit `insufficient` + `minimum_viable_budget` (= root alone)
- **hard vs soft** — `exact`/`scoped` rank above `inferred` (soft), soft is never
  mandatory; `ambiguous`/`unresolved` are never dependencies (`ambiguous` → compact,
  **repo-scoped** hint that never leaks another repo's symbols)
- **monotonicity** — more budget never yields less information (prefix construction)
- **determinism** — same graph + root + budget + policy ⇒ byte-identical bundle
- **diversity** — per-branch diminishing returns stop one subtree eating the budget
- **representation** — a dep is kept at a reduced level rather than dropped, and upgrades
  as budget grows
- **no epistemic escalation** — a soft relation stays soft at any budget

**Approximation quality is measured, not assumed** (vs an exact DP solver on small graphs):
without the diversity penalty the greedy is **≈optimal (median ratio 1.00)**; the diversity
policy costs a measured **~13%** (median 0.87). Every bundle is explainable (per-pick
level/tokens/edge/match/soft/utility/reason; excluded reasons; metrics incl.
`branch_concentration`, `minimum_viable_budget`).

```bash
python3 -m contextruntime.cli bundle <symbol_or_qualified_name> --db graph.db --budget 2048
```

## SemanticFS read surface (Phase 2.3)

This is where the planner **becomes a context compiler**. The bundle chose *which*
symbols at *what* level; the materializer renders **actual source-derived text** for that
selection and a validator enforces the budget against the *rendered* tokens — not the
planner's metadata estimate.

**Representation materializer** (`render_symbol`) renders a symbol at a level over its
source lines as **strictly nested sets**, so representations satisfy *content
monotonicity* by construction:

```
lines(identity) ⊆ lines(signature) ⊆ lines(skeleton) ⊆ lines(slice) ⊆ lines(implementation) ⊆ file
  L0 identity        qualified name only (no source)
  L1 signature       declaration/header line(s)
  L2 skeleton        header + structural lines (control flow / calls), bodies elided
  L3 slice           skeleton + a contiguous relevant region
  L4 implementation  full symbol source
  L5 file            whole file (escalation only; not materialized here)
```

Source is captured at index time (each adapter now stores the symbol's segment, **redacted
and bounded** in the CAS keyed by content hash) and elided lines render as an explicit
`# … N lines …` marker — readable without inventing content.

**Rendered budget validator** — `read_symbol` plans a budgeted neighborhood, materializes
it, then shrinks the *least-important* representation until the **rendered** total fits `B`.
Deps are downgraded first (soft, then ascending utility); the **root is downgradable too**
(last), so even a tiny budget yields a valid identity-level result rather than overflowing.
It reports **Planned-vs-Rendered Error (PRE)** = `|rendered − planned| / rendered`, keeping
the estimate↔reality gap visible rather than hidden.

Verified properties (tests in `tests/test_semanticfs.py`):

- **content monotonicity** — `lines(signature) ⊆ … ⊆ lines(implementation)`, strict at the ends
- **real source** — `read_symbol` returns actual fixture code (`def process`, a real dep call), not bundle metadata
- **rendered budget invariant** — `rendered_estimate ≤ B` for B ∈ {60, 120, 300, 1000} (validated, not assumed)
- **shrink under pressure** — a tight budget downgrades the root to a level no higher than the roomy bundle's, still within budget
- **handles not dumps** — `context_search` / `find_callers` return `ctx://symbol/<id>` handles; the model pages via `read_symbol` / `context_expand`, never a code dump
- **progressive expansion** — `ctx://symbol/<id>[@level]` resolves to rendered source; unknown/expired handles are reported, never silently empty
- **PRE reported** — every adaptive read carries `planned_vs_rendered_error` + `estimator`

```bash
python3 -m contextruntime.cli read-symbol <symbol> --db graph.db --budget 2048
python3 -m contextruntime.cli read-symbol <symbol> --db graph.db --resolution signature
python3 -m contextruntime.cli find-callers <symbol> --db graph.db
python3 -m contextruntime.cli search <query> --db graph.db
python3 -m contextruntime.cli expand 'ctx://symbol/<id>@skeleton' --db graph.db
```

### Measurement & admission hygiene (Phase 2.3.1)

A correctness pass so the Gate-2 numbers mean what they say. No architecture change.

- **The budget is the *serialized* budget.** The validator now counts the full model-visible
  payload — section headers, `ctx://` handles, `match/soft` + `⟪materialization⟫` annotations,
  and the ambiguity block — not just source bodies. The invariant is
  `tokens(serialized response) ≤ B`, and `to_text()` and the validator share one `_serialize`
  so they can't drift. Every path (adaptive, fixed-resolution, no-deps) is enforced; a starved
  budget downgrades even a fixed-resolution read rather than overflowing.
- **PRE measures estimator error alone.** `planned_vs_rendered_error` is now
  `|planned − materialized|/materialized` with *both* taken **before** shrinking, so it no longer
  conflates a wrong planner estimate with deliberate downgrades. The shrink is reported separately
  (`shrink_ratio`, `sections_downgraded`, `sections_dropped`, `root_downgraded`), and a no-planner
  read reports PRE = 0, not 1.
- **`safety_margin` is real.** Planning/materialization aim for `target = ⌊B(1−m)⌋` (reserving
  envelope headroom for the MCP/tool wrapper); `B` stays the absolute ceiling.
- **Bare handles don't leak bodies.** `context_expand("ctx://symbol/<id>")` expands to a bounded
  **signature**; the full body requires an explicit `@implementation`. The suffix is parsed with
  `rpartition("@")` and honored **only if it is a known level** — so a symbol_id that itself contains
  `@` (npm scoped paths, annotations) stays intact and no `@<junk>` can escalate. `render_symbol`
  also coerces any unknown level to `signature` (defense in depth). Closes the
  search → handle → whole-file-dump policy bypass.
- **No bounded prefix is called "implementation".** `render_symbol` reports
  `materialization_quality` ∈ {`complete_ast`, `complete_tree_sitter`, `declaration_only_heuristic`,
  `truncated`, `unverified`} (mirrored in `provenance.source.complete`). Completeness is asserted
  **only from the truthful full-size signal** — `byte_size` (raw source bytes, ≥ char count), *not*
  the redacted sample length, since redaction can shrink a truncated prefix below the char cap and
  mask it. `byte_size ≤ CAP` ⇒ the whole source was stored (complete); `> CAP×4` ⇒ definitely
  truncated (even a single huge line); in between ⇒ `unverified`. A line-count shortfall vs the
  declared span is an additional signal, and implementation/file levels carry an explicit in-text marker.
- **Python call scoping matches tree-sitter.** The `ast` adapter no longer attributes a nested
  function's calls to its enclosing function (the old `ast.walk` bug), and definitions are emitted at
  **every** scope — module, class body, and function — descending through `if`/`for`/`with`/`try`,
  so a def/class hidden in a control-flow block is never dropped. Non-nested code produces identical
  symbols+edges (no regression); traversal is source-ordered for determinism. Feeds Gate-2A ground truth.

Each of the three items above was **found by an adversarial verification pass** (independent skeptics
running snippets against the code) after the first cut shipped a plausible-but-incomplete version, then
fixed and re-verified — the same find → verify → fix loop the runtime is meant to support.

**Honest finding surfaced by the accounting:** once handles are counted, the current verbose handle
form (`ctx://symbol/<repo>::<path>::<qname>`) dominates small budgets — at `B≈120` the response is
almost all headers, forcing every section to identity. Handle compaction (short per-response ids +
a legend) is a candidate follow-up (2.3.2) now that the cost is visible rather than hidden.

## Not in Phase 2 (deliberately)

No embeddings, no runtime/co-change/cross-repo graphs, no graph DB. Phase 2's question
is narrow — *can structural semantic reads reduce file-read context?* — and vector search
would muddy which lever produced the savings. Add it only if NL search proves a bottleneck.

## Next

With the read surface landed (planner → compiler, rendered-budget-validated), what remains
for the Phase-2 gate is: **read classification** (exploration vs edit_precondition, so the
runtime knows when a cheap slice is safe vs when the full precondition must be admitted),
then **Gate 2A** — empirical precision/recall against a per-language ground-truth set
(~100 relations/lang) to replace assigned confidence priors with measured quality — and
**Gate 2B**, the A/B/C/D experiment that produces the first whole-task token number.
