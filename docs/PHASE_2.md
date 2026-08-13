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
| `context_search` · `read_symbol` · `read_slice` · `find_callers` (MCP surface) | ▶ next |
| Budgeted `DEPENDS_ON` bundle generator (`argmax utility s.t. tokens ≤ B`) | ▶ next |
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
| `exact` | dst is a full qualified name | narrow bundle |
| `scoped` | resolved in the src's class/module scope (`self.method`, same-module) | narrow bundle |
| `inferred` | exactly one repo-wide symbol has that short name | single-candidate guess |
| `ambiguous` | **multiple candidates — resolver refuses to pick one** (`ambiguous:<name>`, `ambiguity_count`) | do not invent a dependency; may show candidate signatures |
| `unresolved` | external/dynamic — no repo symbol | no dependency |

`DEPENDS_ON` is derived **only** from `exact`/`scoped`/`inferred` — never `ambiguous` or
`unresolved`. The critical property (verified by an adversarial fixture): a call to
`save()` when `payments.repo.save` and `users.repo.save` both exist is marked `ambiguous`,
not resolved to whichever happened to be indexed first.

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

## Not in Phase 2 (deliberately)

No embeddings, no runtime/co-change/cross-repo graphs, no graph DB. Phase 2's question
is narrow — *can structural semantic reads reduce file-read context?* — and vector search
would muddy which lever produced the savings. Add it only if NL search proves a bottleneck.

## Next

Budget the `DEPENDS_ON` closure (`argmax utility s.t. tokens ≤ B` — select context, never
inflate it), the `read_symbol` progressive-resolution surface (L0 id … L5 file), read
classification, then the A/B/C/D experiment that produces the first whole-task number.
