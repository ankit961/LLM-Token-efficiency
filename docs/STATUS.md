# Status

Honest separation of **implemented** (code exists, unit/invariant-tested) from
**gate-passed** (validated by a real experiment). An evidence-gated roadmap only
gets to claim a gate once a measured trial clears it.

| Item | State |
|---|---|
| Phase 0 — historical measurement (batch profilers) | ✅ implemented |
| Phase 0b-A — residency graph + two-ledger accounting | ✅ implemented |
| Phase 0b-B — live attribution (OTel + hooks; decompose the 39% unattributed; `doctor` live probe) | ◐ pending |
| Phase 1-A — reducer engine + retention + handles | ✅ implemented |
| Phase 1-B — causal task validation (reduced vs raw preserves success) | ◐ pending → folds into Phase 2 |
| Phase 2 — SemanticFS + Graph-Lite | ▶ next |
| Phase 2 — admission experiment (A/B/C/D) | ★ first product gate |

**What the ~53% number is and isn't.** `reduce-scan` reports *~53% reduction on
reducible tool results, Grade C, observe mode.* It excludes source reads, history, the
fixed base prefix, tool schemas, ordinary conversation, and retries — and there is no
same-task raw-vs-reduced agent trial yet. It is **not** "ContextRuntime reduces tokens
53%." The first legitimate whole-task number comes from the Phase-2 A/B/C/D trial.

## Pre-Phase-2 hardening — DONE

Fixed the foundational issues that would otherwise make the experimental ledger itself
questionable:

- **Idempotent re-ingest.** Session-scoped ids + `session_id` columns + a UNIQUE edge
  index + `delete_session` on re-ingest. Re-ingesting a transcript no longer duplicates
  nodes or edges (verified on real data: identical counts after re-run).
- **Cross-session isolation.** `content_id` is `"<session>::obj:<hash>#<idx>"`; identical
  content in different sessions no longer collides, and a session can be deleted cleanly.
- **Redaction at rest.** Best-effort secret scrubbing (`redact.py`) runs before any raw
  payload is stored in the CAS or emitted in a reduced summary (AWS/GitHub/OpenAI/Slack/
  Google keys, JWTs, bearer tokens, PEM blocks, `*_SECRET/TOKEN/PASSWORD=…`).
- **Handle resolution.** `context_expand(result://<hash>)` resolves a handle to its
  (redacted, bounded) payload and reports expiry explicitly — a reduced result is never
  an unfollowable pointer. This is the first SemanticFS primitive.

Known remaining (Phase 2): full sidechain/subagent stream-keying `(session_id, agent_id)`;
schema-version migrations (currently fails loudly on mismatch).

## Phase 2 — the plan (focused; not a repo-intelligence platform)

**Phase 2.1 — resolver correctness · DONE** (before any bundle generator): package-qualified
module identity; scope-aware resolution with an explicit `match_kind`
(`exact/scoped/inferred/ambiguous/unresolved`) that **never picks `candidates[0]`**;
`DEPENDS_ON` derived only from dependable matches; tree-sitter scope + recursive calls;
`UNIQUE(src,dst,type,resolution)` so AST/SCIP/LSP evidence can coexist; report renamed to
*Structural Confidence Report* (assigned prior ≠ measured quality); adversarial ambiguity
tests; CI (Python 3.10–3.13, core + `[codegraph]` + fallback). Remaining: import-aware
resolution, `REFERENCES` (schema-reserved), richer tree-sitter relationship coverage.

1. Harden graph foundation ✅
**Phase 2.2 + 2.2.1 — budgeted bundle planner · DONE**: `build_bundle` (symbol × representation-level, deterministic monotone greedy approximation; **mandatory = root only**, hard eligible-not-mandatory, soft never mandatory, ambiguous→repo-scoped hint; per-symbol cost-ladder; budget-pressure signal). Approximation measured vs exact DP: ≈optimal without diversity (median 1.00), diversity costs ~13% (0.87). It is a **planner, not a compiler** — Phase 2.3 materializes actual source text + validates the rendered budget. MCP read surface next.

2. `CodeSymbol` schema (id, repo, language, kind, qualified_name, path, lines, signature,
   content_hash, parser, **resolution_quality**) ✅
3. Language adapters (tree-sitter / LSP / SCIP) — typed strong, dynamic best-effort
4. Graph-Lite edges: `CONTAINS · IMPORTS · REFERENCES · CALLS · IMPLEMENTS · TESTED_BY ·
   DEPENDS_ON`, **each with `confidence` + `resolution` provenance**
5. **Budgeted** DEPENDS_ON bundle generator (`argmax utility s.t. tokens ≤ B`) — selects
   context, never inflates it
6. Read classification: `exploration / edit_precondition / verification / config / unknown`
   — retrospective ground truth (a read followed by an edit of the same path within a
   causal window = `edit_precondition`); the published denominator is exploration-only
7. MCP surface: `context_search · read_symbol · read_slice · find_callers · context_expand`
   with progressive resolution (L0 id … L5 file)
8. PreToolUse deny+nudge adapter (admission)
9. **A/B/C/D experiment harness** — A native · B ContextReduce · C semantic available ·
   D semantic-first enforced — retry-inclusive
10. Per-language + per-edge-source bundle-quality report

**No embeddings in Phase 2** — keep the experiment clean (was it the graph, retrieval,
embeddings, or reranking?). Add vector search only if NL search proves a bottleneck.

### Metrics that decide the gate

- **Net Token Efficiency** (retry-inclusive): `1 − T_optimized_all_attempts / T_baseline_all_attempts`.
- **Context Bundle Sufficiency (CBS)** — P(agent proceeds without an immediate expansion),
  reported per language.
- **Context Expansion Debt (CED)** — tokens of follow-up context caused by under-bundling;
  `BundleNetSaving = baseline_read_residency − (bundle_residency + CED)`. Headline metric.
- **Organic adoption** (arm C) and **enforcement compliance** (arm D).
- Δ decomposition: `ΔReduce=B−A`, `ΔAdoption=C−B`, `ΔEnforce=D−C`.

### Two gates

- **2A retrieval viability** — ~100 known relationships/language: precision, sufficiency
  recall, bundle token size, lookup latency (targets, not results: typed recall ≥90%,
  dynamic ≥75–80%, P50<150ms/P95<500ms).
- **2B agent viability** — 30–50 real tasks × A/B/C/D: **≥30% overall reduction on the
  context-intensive subset** with no catastrophic success loss, retries not erasing
  savings, wall-time ≤~1.2× (this is G1, not the final 50% claim).

If 2B passes, invest in CachePolicy then the Phase-4 capsule. If it fails, the measured
reason (low adoption, weak dynamic-language graph, expansion debt, unavoidable native
reads, or insufficient prefix shrink) says whether to fix the Personal architecture or
pivot early to gateway/enterprise.
