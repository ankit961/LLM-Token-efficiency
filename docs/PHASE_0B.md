# Phase 0b — ContextScope + the Context Residency Graph

Phase 0b is the first **committed** engineering work (design v1.2 §14, C12). It turns
the one-shot batch profilers under [`contextscope/`](../contextscope/) into an
incremental, graph-backed package: transcripts are ingested into a content-addressed
**residency graph** in SQLite, and the occupancy/economic ledgers are computed as
**queries over that graph** rather than ad-hoc passes.

> Graph-Lite is mandatory runtime infrastructure (C1). This is the
> context-object/residency graph — SQLite edge tables, no Neo4j.

## Module map

| Module | Role | Status |
|---|---|---|
| [`model.py`](../contextruntime/model.py) | Durable objects, each `schema_version`-stamped (C13) | ✅ functional |
| [`schema.sql`](../contextruntime/schema.sql) | Residency-graph DDL: typed node tables + one `edges` catalog + CAS `blobs` | ✅ functional |
| [`store.py`](../contextruntime/store.py) | SQLite store; upserts, edge writes, schema-version guard | ✅ functional |
| [`ingest.py`](../contextruntime/ingest.py) | JSONL → normalized session; **reconcile by `requestId`** (load-bearing source) | ✅ functional |
| [`residency.py`](../contextruntime/residency.py) | Build `Request`/`ContextObject` nodes + `RESIDENT_IN`/`DUPLICATE_OF`/`MATERIALIZED_FROM`/`BROKE` edges | ✅ functional |
| [`ledger.py`](../contextruntime/ledger.py) | Occupancy (exact) + economic (priced) + token-turns by kind + Tier-A redundancy | ✅ functional |
| [`doctor.py`](../contextruntime/doctor.py) | Capability probe → `CapabilityProfile` + evidence grade, stamped on reports (C11) | 🟡 structured stub |
| [`cli.py`](../contextruntime/cli.py) | `ingest` / `ledger` / `doctor` / `graph` | ✅ functional |

**Deferred (tables + dataclasses exist; logic lands later):** `DEPENDS_ON` (Phase 2,
SemanticFS/Graph-Lite), `REDUCES` and `IN_CAPSULE` + `EvidenceNode`/`Capsule` (Phase 4,
Task/Evidence graph). The shapes are frozen now so durable state survives upgrades.

## What's honest about it

- **Occupancy is exact** (reconciled from per-request usage); **token-turns and
  category attribution are estimates** (chars/4 tokenizer, residency span = entry-turn
  → next compaction/session end).
- **Cache islands / `BROKE` are estimates** (design §6): the real cache key is prefix
  bytes the JSONL doesn't expose. The `cache_read`-collapse heuristic can't distinguish
  a rebuild from a large new turn; treat island counts as hints, not measurements.
- **Dollar figures use placeholder prices** in [`pricing.json`](../pricing.json) — verify
  before quoting. Token figures are unaffected.
- **Doctor is a stub**: its capability values are the design's working assumptions
  (Appendix B), each `?` = verify-at-runtime. Live probing needs the hook/MCP adapters
  (Phase 2), so nothing is asserted as confirmed and the evidence grade stays **C**.

## Run it

```bash
# ingest into a persistent graph, then query the ledger
python3 -m contextruntime.cli ingest ~/.claude/projects/*/*.jsonl --db graph.db
python3 -m contextruntime.cli ledger --db graph.db
python3 -m contextruntime.cli graph  --db graph.db      # node/edge counts
python3 -m contextruntime.cli doctor                    # capability profile

# one-shot on a single transcript (in-memory, prints the ledger)
python3 -m contextruntime.cli ingest tests/fixtures/synthetic_session.jsonl

# tests (synthetic fixture, no private data)
python3 -m pytest -q
```

`*.db` is gitignored; graph output stays local. The ledger and graph commands emit
only aggregates — no prompt/source/tool content.

## Data model → design §9

Nodes `ContextObject · Request · CacheIsland · Source · (Phase-4) Capsule/EvidenceNode`;
edges `RESIDENT_IN · MATERIALIZED_FROM · DUPLICATE_OF · SUPERSEDES · REDUCES · CACHES ·
BROKE · IN_CAPSULE · DEPENDS_ON`. Each delivery is a distinct content-addressed node;
the CAS (`blobs`) dedups the bytes; a re-delivery of identical content is a new node
linked by `DUPLICATE_OF` to the first instance.

## Next

- **Phase 1 (ContextReduce):** `REDUCES` edges + reducer contracts (PostToolUse output
  reduction; version-gated — the doctor gates whether it's available).
- **Phase 2 (SemanticFS + Graph-Lite):** the `CodeSymbol`/`DEPENDS_ON` graph, plus the
  `exploration_read` vs `edit_precondition_read` split (C10) and bundle precision/recall
  by language (C3).
