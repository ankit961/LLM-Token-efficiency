# G1 — Graph-First Context Compilation: Step-1 audit (before implementation)

Question G1 must answer: *can deterministic local anchor resolution + graph-constrained source
compilation deliver the exact code an agent later needs at substantially lower model-visible token
cost than native exploratory Read/Grep — independently of whether the agent voluntarily chooses a
semantic tool?* This is NOT the 0/11 advisory SemanticFS experiment (that measured voluntary adoption
and remains valid for that narrow question). Offline go/no-go only; no live quota; do not touch frozen
B1 search-reduction or the B2 evidence artifacts.

## 1. Can the current graph resolve an anchor from…

| anchor input | today | gap |
|---|---|---|
| exact `path + line` | **no primitive** | schema HAS `path, start_line, end_line` → add `symbol_at` (Step 2) |
| `path + symbol` | partial (`_resolve` by name, unscoped) | scope `_resolve_candidates` to a path |
| bare symbol | **yes** — `semanticfs._resolve` / `_resolve_candidates` (exact id → qualified_name → suffix, ranked) | — |
| traceback frame | **no** | add a `File "…", line N, in fn` parser → `path:line` → `symbol_at` |
| only a known file | partial (`SELECT … WHERE path=?`) | add a file→root heuristic (module/top symbol) |
| only free-text bug language | **weak** — `context_search`/`search_symbols` is structural name/path substring, not a bug-description resolver | add local lexical candidate generation (identifier extraction → symbol-name match), graph-reranked |

## 2. What already exists (reuse, do not duplicate)

- **The budgeted context COMPILER**: `semanticfs.read_symbol` → `codegraph.bundle.build_bundle`
  (BFS over `CALLS/IMPLEMENTS/IMPORTS`, HARD>SOFT, budget-monotone) → `codegraph.render.render_symbol`
  (levels identity→signature→skeleton→slice→implementation). It enforces the **serialized** token
  budget and **downgrades the ROOT LAST** — i.e. it already preserves the target implementation when
  the budget permits. This is exactly the invariant G1 needs and the OPPOSITE of B2's skeletonization,
  which dropped the target body.
- Bare-symbol resolution, `find_callers` (`code_edges_to CALLS`), `read_slice`, `context_search`.
- **Symbol bodies are stored as blobs in the graph DB** (30,953 blobs; a symbol's `content_hash` →
  its source `sample`). So `render_symbol` materializes source **offline** with no worktree. Coverage:
  **97.7% of blobs are complete**; **2.3%** (the largest symbols, e.g. a 394-line function) are
  byte-capped (`materialization_quality="truncated"`) — the harness will flag truncated targets so
  edit-line recall is not silently overstated.
- Schema: `symbols(symbol_id, repo_id, language, kind, qualified_name, path, start_line, end_line,
  signature, content_hash, parser, resolution_quality)` + `code_edges` + `blobs`.

## 3. Minimum additions for G1

1. `symbol_at(store, path, line, repo_id)` — narrowest enclosing symbol (Step 2).
2. Anchor resolvers: traceback-frame parser, path-scoped symbol, file→root, free-text→lexical
   candidates (Step 4; lexical stays OUTSIDE model context — an indexing primitive).
3. `context_compile(store, *, path, line, symbol, query, budget, repo_id)` — resolve anchor, then
   reuse `read_symbol`; preserve the target implementation (Step 3).
4. Offline G1 replay harness + metrics (edit-symbol / edit-line / useful-read recall, token
   compression, counterfactual calls) + ablations (native / lexical-only / graph-first / skeleton) +
   budget sweep (512/1024/2048/4096) + preregistered go/no-go (Steps 5–8).

## 4. Data for the offline replay

- **Trajectories**: the 60 native (A_native) Step-7 transcripts (reads-with-content + edits-with-
  old_string) — ground truth for what the agent eventually edited/read.
- **Graphs**: the pilot per-task `C_graph/codegraph.db` (indexed at each task's base_commit;
  provenance-verified) — anchor resolution + offline source materialization.
- **No future leakage**: anchors are generated only from the problem statement / early trajectory;
  the edited symbol + subsequent reads are held out as ground truth.

## 5. Guardrails (do not)

Do not modify frozen B1 behavior; do not touch B2 evidence artifacts; do not reopen advisory
SemanticFS; do not claim token savings from the offline counterfactual (label them opportunities);
do not run any live Claude experiment until this offline result is reviewed.
