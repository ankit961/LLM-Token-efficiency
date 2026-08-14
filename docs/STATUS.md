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
| Phase 2.1 / 2.1.1 — resolver correctness + safety (Graph-Lite) | ✅ implemented |
| Phase 2.2 / 2.2.1 — budgeted bundle planner | ✅ implemented |
| Phase 2.3 — SemanticFS materializer + read surface (library/CLI) | ✅ implemented |
| Phase 2.3.1 — measurement & admission hygiene | ✅ implemented |
| Phase 2.4-A/B — API contract + `SemanticReadEvent` telemetry + **MCP stdio transport** (observe-only) | ✅ implemented |
| Phase 2.4-B.1 / .1.1 / .1.1a — transport measurement + event-identity + replay-race correctness | ✅ implemented |
| Phase 2.4-C — retrospective labeller + **HookJournal** live capture (hook_schema **0.3.0**, contract **frozen**) | ✅ implemented |
| Gate 2A — retrieval viability (ground-truth precision/recall) | ◐ after 2.4 |
| Gate 2B — admission experiment (A/B/C/D) | ★ first product gate |

> **Transport note.** The SemanticFS read surface is now exposed over a **hand-rolled MCP stdio
> transport** (`contextruntime mcp --db …`, no third-party dependency), and every materializing
> call (`read_symbol`/`read_slice`/`context_expand`) emits a durable `SemanticReadEvent` — the
> transport is instrumented from the start. It is **observe-only**: nothing is denied, and the
> classification/outcome columns stay null until the 2.4-C labeller fills them.

> **HookJournal freeze.** The prospective observation layer (`hookjournal.py`, `normalize.py`)
> and its labeller contract (`classify.py`) are **frozen at hook_schema 0.3.0** after slice 2.1.2.
> It is a **separate** SQLite store from the frozen B.1 GraphStore, metadata-only, fail-open (a
> journal error never blocks a tool call), and replayable (capture is decoupled from
> normalization, so windows can change without recapturing). Fixed through three adversarial
> review passes (2.1 real-payload contract, 2.1.1 evidence-integrity, 2.1.2 observed-vs-claimed
> boundary). The next slices consume this contract without changing it: the `cr-hook` stdin CLI +
> `settings.json` wiring + a live smoke run, then observed-label reports and window sensitivity.

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
**Phase 2.2 + 2.2.1 — budgeted bundle planner · DONE**: `build_bundle` (symbol × representation-level, deterministic monotone greedy approximation; **mandatory = root only**, hard eligible-not-mandatory, soft never mandatory, ambiguous→repo-scoped hint; per-symbol cost-ladder; budget-pressure signal). Approximation measured vs exact DP: ≈optimal without diversity (median 1.00), diversity costs ~13% (0.87). It is a **planner, not a compiler** — Phase 2.3 materializes actual source text + validates the rendered budget.

**Phase 2.3 — SemanticFS materializer + read surface · DONE**: the planner became a **context compiler**. `render_symbol` materializes actual source-derived text at L0…L4 over strictly-nested source-line sets (content monotonicity by construction); `read_symbol` plans → materializes → validates the *rendered* budget; `read_slice · find_callers · context_search · context_expand` round out the surface (search/callers return handles, never code dumps). Library + CLI only — **no MCP transport yet** (folded into 2.4).

**Phase 2.3.1 — measurement & admission hygiene · DONE** (found + fixed via three adversarial verification passes): the budget is the **serialized** model-visible payload (not just source bodies), **PRE** isolates estimator error from deliberate shrink, `safety_margin` is actually applied, bare handles resolve to signatures (explicit `@implementation` to escalate; unknown `@levels` can't leak a body), and `materialization_quality` never passes a bounded/heuristic body off as complete. Python call-scoping reached tree-sitter parity (nested + control-flow-hidden defs, own-scope call attribution). **Honest finding:** verbose handles dominate small budgets — Phase 2.4 measures `ProtocolOverheadRatio` before deciding whether handle compaction (candidate 2.4.1/2.3.2) is worth it.

**Phase 2.4-A/B — read-surface contract + telemetry + MCP transport · DONE** (observe-only): progressive `@next`
expansion handles (not a jump to the full body); `@file` rejected until real whole-file materialization exists;
`ProtocolOverheadRatio` measured per read; durable `semantic_reads` (schema 0.5.0) with `record_read`/`record_expansion`
kept separate from the pure read functions and **expansion→parent linkage so CED sums directly**; a hand-rolled MCP
stdio transport (`contextruntime mcp`) that emits an event on every materializing call, committed per call for durability.
Nothing is denied; classification/outcome columns are null.

**Phase 2.4-B.1 — transport measurement correctness · DONE** (so 2.4-C consumes trustworthy numbers):
(1) **every materialization is logged** — `context_expand` records even without a `parent_event_id`
(attribution is optional, not a prerequisite); (2) **the MCP `meta:` block is in the ledger** —
`transport_content_tokens`/`transport_overhead_tokens` capture the full model-visible response
(semantic payload + transport meta), distinct from the semantic-layer `protocol_overhead`, and CED
sums full transport tokens (a read's meta block cost ~70 tok on 369, an expansion's ~31 on 79);
(3) **concurrency-safe ordering** — `seq` is a SQLite `AUTOINCREMENT` (not `SELECT MAX(seq)+1`, which
two processes could read identically); (4) **truthful MCP negotiation** — the server echoes the client's
protocol version only if supported, else answers with one it supports, and stamps `protocolMode =
legacy-2024-11-05` (the newer MCP spec dropped the initialize handshake; this is compat mode until tested
against real clients). `event_id` stays in the model-visible `meta` for now so the model can
link an expansion to its parent — not hidden into MCP `_meta` until a target client's metadata propagation
is verified (else compaction would break CED attribution).

**Phase 2.4-B.1.1 — event identity · DONE** (schema 0.7.0): `event_id` is now a FRESH id per
materialization (a UUID), because a content hash of (session, request, channel, symbol) collapsed two
genuine materializations (a repeated read, a parentless repeated expansion, or native/Bash reads with an
absent/reused request_id) into one row under `INSERT OR IGNORE` — silently under-counting the events 2.4-C
population-counts. Idempotence is now INTENTIONAL only: an optional `source_event_key` (tool_use_id /
transcript uuid / hook id) dedups a duplicate *delivery*, while an accidental `event_id` collision FAILS
LOUDLY (the insert scopes its conflict clause to `source_event_key`; an `event_id` collision raises).
`seq` remains the DB-assigned order surrogate — allocation order, not gapless — and never appears in linkage.

**Phase 2.4-B.1.1a — replay canonicalization race + producer-key scoping · DONE** (schema 0.8.0): the B.1.1
replay path did a check-then-insert lookup, so two concurrent deliveries of one producer event could each
return their own fresh UUID — one of which lost the insert race and was never persisted, later orphaning a
`parent_event_id`. `put_semantic_read` now returns the **canonical persisted** id atomically (insert scoped to
the producer-key conflict, then read back the winning row), so every concurrent caller gets the same stored id.
The producer key is also **namespaced + session-scoped**: `UNIQUE(source_system, stream_key, source_event_key)`
with a `source_system` domain (`claude_mcp`/`transcript`/`hook`), so the same `tool_use_id` in two sessions is
two events, and different producer domains that reuse an id format don't collide. Mirror test: N concurrent
deliveries of one producer event → exactly one row, every caller returns the same canonical id. **Producer-key
completeness (schema 0.10.0):** a keyed event must carry its whole key with every part NON-EMPTY — a NULL leaves
the composite tuple non-conflicting, and an empty string would dedup unrelated malformed deliveries — enforced
identically at BOTH layers: the recorder's `_require_complete_key` (fail-fast `ValueError`) and a durable DB
`CHECK` that requires `length(trim(...)) > 0` on `source_event_key`/`source_system`/`stream_key`, so a direct
write can't bypass the recorder's domain.

> **Benchmark-store immutability (Gate 2 rule).** The store is deliberately rebuild-only: no migrations,
> so a DB stamped with an older `schema_version` fails loudly on open (C13). That is fine while the
> telemetry schema is still being frozen — regenerate the store. **Once Gate-2A/B measurement begins,
> benchmark stores become immutable evidence artifacts**: a schema change must produce a NEW artifact,
> never silently mutate an existing measurement, so results stay reproducible.

**Phase 2.4-C — read classification · NEXT** (observe-only first, no enforcement): durable `SemanticReadEvent` (identity/channel/target/classification/admission/context/outcome); retrospective **observed** labels (a native read that is the latest eligible edit-precondition for a same-path edit within a causal window — *not* every read before an edit) with an `evidence_grade`/`classification_source` (client-tracker-confirmed / temporal-causal / heuristic); real-time **predicted** labels kept separate so classifier precision/recall is measurable, never assumed; classify **materialization across channels** (native `Read`, Bash `cat/sed/git show/…`, SemanticFS, expansion) reporting exploration-bypass by **events and by tokens**; expansion **parent linkage** so CED is summed directly; `ProtocolOverheadRatio` measured at working budgets; **thin MCP stdio transport** instrumented to emit these events. Enforcement (deny+nudge, high-confidence exploration only) waits for confusion matrices — the dangerous error is P(predicted exploration | actually edit-prerequisite).

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
