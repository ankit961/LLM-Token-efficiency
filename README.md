# LLM Token Efficiency (ContextRuntime)

**Measuring — and then reducing — where AI coding agents actually spend tokens.**

> **Program result (2026-08, frozen):** on live, graded coding sessions the surviving stack —
> **admission control + context-lifetime management** — demonstrated a
> **41.5% end-to-end input-token reduction with non-inferior task quality** (B6, 24 sessions),
> and a **29.3% live dollar reduction** in the gateway configuration, landing **0.2pp from a
> preregistered model prediction** (B8, CLI-billing-confirmed). A calibrated cache-cost model
> puts the giant-long-context regime at **~−60% dollars (modeled, not yet live)**.
> Details: [the results section below](#final-results--the-b-series-2026-08).

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
python3 -m contextruntime.cli index-code path/to/repo --db graph.db  # Phase 2: CodeSymbol graph
python3 -m contextruntime.cli doctor        # runtime capability profile (C11)
python3 -m pytest -q                         # tests
```

The package also ships the production-path runtime the B-series validated:

- **`contextruntime/retirement.py`** — `RetirementPlanner → HistoryMutationPlan → HistoryMutator`
  (policy separated from mechanism; safe-by-construction retirement of superseded/cold tool
  results).
- **`contextruntime/gateway.py` + `gateway_proxy.py`** — a stdlib HTTP gateway
  (`python -m contextruntime.gateway_proxy`, point `ANTHROPIC_BASE_URL` at it) with modes
  `CR_GATEWAY_MODE=off|observe|enforce`, thinking-GC (`CR_GATEWAY_THINKING_KEEP`), and
  response-level fail-open (any upstream 4xx to a mutated body resends the original bytes —
  **0 rejected mutations in 17/17 live enforce sessions**).
- **`contextruntime/cachemodel.py` + `cachealign.py`** — the prefix-cache cost model (calibrated
  exact on live sessions) and the cache-aligned scheduler
  (`CR_GATEWAY_CACHE_ALIGN=off|cold|gated`): fired mutations become persistent/byte-stable;
  new mutations fire only when the cache is cold or a break-even rule clears.
- **`contextruntime/prefixdoctor.py`** — `cr doctor --prefix`: zero-quota capture + per-item
  audit of the fixed prefix (what to KEEP/DEFER/DISABLE, with feasibility tags).
- **`contextruntime/providers.py`** — the framework is **provider-generic**: every algorithm
  reduces to four constants (`read_mult`, `write_mult`, `ttl_s`, `out_mult`), selected by
  `CR_GATEWAY_PROFILE`. One derived number — break-even reads per rewritten token — flips the
  scheduler's verdict between providers (Anthropic-1h: 19, hold on short sessions; free-write
  providers: 1, fire almost always). Cross-provider sensitivity on the same real sessions:
  **[docs/provider-profiles.md](docs/provider-profiles.md)** (only `anthropic-1h` is
  live-validated; the rest are calibration-pending presets).

Earlier phases (**[STATUS.md](docs/STATUS.md)**): 0b residency graph · 1 ContextReduce ·
2 SemanticFS/Graph-Lite — the graph-retrieval line was **closed by measurement** (G1/G2, B5):
the wins live in admission + lifetime, not in out-searching the model.

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

## Final results — the B-series (2026-08)

Every number below is from **preregistered, live, graded experiments on real Claude Code
sessions** (single environment, django/SWE-bench-Verified tasks; treat as design-partner
evidence pending replication on other repos). Full write-ups in `docs/b*-findings.md`;
frozen artifacts and per-session gateway logs in `corpus/analysis/`.

| claim | number | status |
|---|---|---|
| End-to-end **context-workload** reduction (admission + retirement + thinking-GC), quality non-inferior (9 vs 10 of 12 graded successes) | **−41.5%** pooled (per-task −28…−59%) | **LIVE** — B6, 24 sessions |
| **Live dollar** reduction, gateway configuration (admission through proxy + gated scheduler), quality 9/9 vs 9/9 | **−29.3%** (CLI billing agrees: −29.2%) | **LIVE** — B8v2, vs preregistered prediction −29.5% (0.2pp hit) |
| Cache-cost model (1h-tier pricing, partial interior hits, extent semantics) | exact on 11/12 native sessions; 7% median on mutated; **0.2pp** on a frozen live prediction | **LIVE-VALIDATED** — B7/B8 |
| Mutation safety: rejected mutated requests; retirement-caused re-reads | **0 of 17** live enforce sessions; re-reads unchanged (11 vs 12) | **LIVE** — B6+B8 |
| Giant-long-context interactive regime (retirement + thinking dollars) | **~−60%** pooled (median session ≈ 0 — value is tail-concentrated) | **MODELED** — B7 replay over 54 real sessions; not yet live |

**The thesis the data settled:** `token efficiency ≈ admission control + lifetime control` —
control *what enters* the prefix and *how long it stays*. Retrieval sophistication (code graphs,
discovery-packet substitution) was measured and closed: enforced live, eager discovery packets
made sessions **+71.6% more expensive** (B5.3).

**Platform facts discovered en route** (each independently useful):

1. This client requests the **1-hour prompt-cache TTL — cache writes bill at 2.0×** base input,
   which is why naive history mutation saves tokens but not dollars (B6's −41.5% tokens was only
   −2.5% dollars until scheduling fixed it).
2. **A custom `ANTHROPIC_BASE_URL` disables MCP tool-schema deferral** — any gateway deployment
   silently starts ~43k tokens/request behind the native client. **Admission is not an optional
   lever in a gateway product; it is the entry fee** (B8v1).
3. The prompt cache serves **partial interior hits**, and the 1h TTL is **soft** (65-minute idle
   gaps did not expire it live).
4. Claude Code stores one API call as several transcript records sharing a `requestId` — usage
   analysis must merge them (`corpus/transcript_util.merged_records`) or per-turn numbers
   inflate ~1.9×.

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

**B-series complete and frozen** (B1–B8; research lines B1/B2/G1/G2/B5 closed by measurement).
Live-demonstrated: 41.5% context workload (B6) · 29.3% gateway dollars (B8) · scheduler no-harm
and mutation safety (17/17). Remaining modeled-only claim: the ~−60% giant-session regime (B7),
whose validation requires a live run in that regime. The experiment log, in order:
`docs/b3-findings.md` → `b3.1/b3.2` → `B3_DECISION.md` → `path-to-50.md` → `prefix-doctor-findings.md`
→ `call-collapse-findings.md` → `joint-stack-findings.md` → `executor-ab-findings.md` →
`b6-protocol.md`/`b6-findings.md` → `b7-findings.md` → `b8-protocol.md`/`b8-findings.md`.

---

*Engineering posture: version-gated, inferred, best-effort, fail-open, or
verify-at-runtime where the platform doesn't guarantee behavior. The design doesn't
claim to control things it can't.*
