# LLM Token Efficiency

**Measuring — and then reducing — where AI coding agents actually spend tokens.**

Agentic coding is a loop: every model request re-sends the whole conversation as its
prompt prefix, so a token you admit once is re-billed on *every* later turn. The unit
of cost isn't the token — it's the **token-turn** (a token multiplied by the number of
turns it stays resident). This project measures that cost precisely from real session
transcripts, then builds a runtime that controls what becomes prefix, at what
resolution, and for how long.

> **Master lever:** prefix size. Shrinking it makes both the per-turn cache re-reads
> *and* the unavoidable cache rebuilds cheaper at once.

## What's here

### `contextruntime/` — Phase 0b package (ContextScope + the Context Residency Graph)

The committed foundation: transcripts are ingested into a content-addressed
**residency graph** in SQLite, and the ledgers are computed as **graph queries**.
See **[docs/PHASE_0B.md](docs/PHASE_0B.md)**.

```bash
python3 -m contextruntime.cli ingest ~/.claude/projects/*/*.jsonl --db graph.db
python3 -m contextruntime.cli ledger --db graph.db
python3 -m contextruntime.cli reduce-scan --db graph.db   # Phase 1: what ContextReduce would save
python3 -m contextruntime.cli doctor        # runtime capability profile (C11)
python3 -m pytest -q                         # smoke tests (synthetic fixture)
```

Phases so far: **[0b — ContextScope + Residency Graph](docs/PHASE_0B.md)** ·
**[1 — ContextReduce](docs/PHASE_1.md)** (PostToolUse output reduction; ~53% on
reducible tool results in observe mode).

### `contextscope/` — Phase 0 batch profilers (reference)

The original one-shot analyzers the package is refactored from. Local, read-only over
Claude Code's own transcripts (`~/.claude/projects/**/*.jsonl`); they emit **aggregates
only** — no prompt, source, or tool-output content leaves the machine.

| Tool | What it measures |
|---|---|
| [`contextscope.py`](contextscope/contextscope.py) | Dual ledgers — **occupancy** (attention: `input + cache_read + cache_creation`) and **economic** (priced via [`pricing.json`](pricing.json), never hard-coded ratios). Token-turns, category attribution, strict-tier waste. |
| [`cachescope.py`](contextscope/cachescope.py) | Prefix **cache-break lifecycle** — detects rebuilds and attributes each to a cause (TTL / model-switch / compaction / version). |
| [`cachescope_lineage.py`](contextscope/cachescope_lineage.py) | Lineage-aware v0.2 — walks the true `parentUuid` chain, computes the honest *geometric* recache, and splits churn into **avoidable vs. unavoidable**. |
| [`modelswitch.py`](contextscope/modelswitch.py) | Model-switch ("context migration") churn — resident context at each switch, cache-island warmth, migration cost. |

### Measured findings

From the author's own **~168 Claude Code sessions / ~170k requests** — a
design-partner sample, **not** a market benchmark (treat every number as
internal/design-partner evidence until independently replicated):

- **96%** of context occupancy is cache re-reads — the loop multiplier.
- **82%** of cache-write tokens are prefix **rebuilds**, not new content
  (of which ~64% is unavoidable TTL-idle; ~24% is subscription-addressable).
- Aged tool results still resident >50 turns ≈ **23%** of occupancy.
- Strict hash-identical re-delivery only **2.4%**; model-switch churn only **2.7%**;
  rewrite amplification **1.3×** (edits are already patches).
- Occupancy concentrates: the **top 20 sessions = 74%** of it.

## Run it

```bash
python3 contextscope/contextscope.py            # full corpus  -> reports/report.md + .json
python3 contextscope/cachescope_lineage.py      # cache-break lifecycle
python3 contextscope/modelswitch.py             # model-switch churn
```

Flags: `--since-days N`, `--max-files N`, `--projects-dir PATH`, `--out DIR`.
Dollar figures use placeholder prices in `pricing.json` — **verify against current
provider pricing before quoting**; token figures are unaffected.

### Privacy

The profilers read local transcripts and write only category counts, token estimates,
and top-offender paths into `reports/`. **`reports/` is gitignored** — it contains real
local file paths and is never committed.

## Design & roadmap

- **[ROADMAP.md](ROADMAP.md)** — the evidence-gated build order (profiler → reduce →
  admission → cache stability → capsule → policy → gateway → enterprise; advanced graph
  retrieval deferred).
- **[docs/explainer.html](docs/explainer.html)** — the token-economics problem and the
  levers, visually.
- **[docs/context-object-graph.html](docs/context-object-graph.html)** — the runtime's
  core data model (context objects as nodes; every edge is a number the profiler
  computes).
- **[docs/implementation-session-integration.html](docs/implementation-session-integration.html)** —
  how the runtime wires into a Claude Code session (hooks, MCP, daemon), with every
  version-gated capability marked *verify-at-runtime*.

## Status

Foundation frozen; building **Phase 0b → 1 → the Phase-2 admission experiment**.
Everything past Phase 2 stays evidence-gated — measured before built.

---

*Engineering posture: version-gated, inferred, best-effort, fail-open, or
verify-at-runtime where the platform doesn't guarantee behavior. The design doesn't
claim to control things it can't.*
