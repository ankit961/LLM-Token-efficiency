# Phase 2 — SemanticFS + Graph-Lite (in progress)

Phase 2 is the **first real product gate**. This increment lands the foundation:
the **CodeSymbol graph** (Graph-Lite, C1) with language adapters and per-edge
confidence/provenance. Later increments add the budgeted bundle generator, read
classification, the MCP read surface, and the A/B/C/D experiment — see
[STATUS.md](STATUS.md) for the full plan and gates.

## What's in this increment

| Piece | Status |
|---|---|
| `CodeSymbol` schema + `symbols`/`code_edges` tables (repo-scoped, idempotent re-index) | ✅ |
| Python adapter via **stdlib `ast`** (exact, zero-dependency, high confidence) | ✅ |
| **tree-sitter** adapter (JS/TS/Go/Java/Rust when the grammar is installed) | ✅ |
| Regex **heuristic** fallback (low confidence) | ✅ |
| Graph-Lite edges `CONTAINS · IMPORTS · CALLS · IMPLEMENTS · TESTED_BY · DEPENDS_ON`, each with **confidence + resolution** | ✅ |
| Per-language + per-resolution **bundle-quality report** (C3) | ✅ |
| `context_search` · `read_symbol` · `read_slice` · `find_callers` (MCP surface) | ▶ next |
| Budgeted `DEPENDS_ON` bundle generator (`argmax utility s.t. tokens ≤ B`) | ▶ next |
| Read classification (exploration vs edit_precondition …) | ▶ next |
| A/B/C/D experiment harness | ▶ next |

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
lower — and lower still for dynamic languages. Unresolved targets become
`unresolved:<name>` with their confidence discounted, so the (coming) bundle generator can
tell solid edges from guesses.

## Run it

```bash
python3 -m contextruntime.cli index-code path/to/repo --db graph.db
# Python needs no extra deps. For JS/TS/Go/Java/Rust at high confidence:
pip install -e ".[codegraph]"
```

Example (this repo's package + a JS sample):

```
symbols : {'javascript': 4, 'python': 7}
parsers : {'javascript': 'tree_sitter', 'python': 'python_ast'}
bundle quality by language (mean edge confidence — the C3 signal):
  javascript   conf=0.90  parser=tree_sitter
  python       conf=0.78  parser=python_ast
```

## Not in Phase 2 (deliberately)

No embeddings, no runtime/co-change/cross-repo graphs, no graph DB. Phase 2's question
is narrow — *can structural semantic reads reduce file-read context?* — and vector search
would muddy which lever produced the savings. Add it only if NL search proves a bottleneck.

## Next

Budget the `DEPENDS_ON` closure (`argmax utility s.t. tokens ≤ B` — select context, never
inflate it), the `read_symbol` progressive-resolution surface (L0 id … L5 file), read
classification, then the A/B/C/D experiment that produces the first whole-task number.
