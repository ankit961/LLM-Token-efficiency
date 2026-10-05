# LLM Token Efficiency (ContextRuntime)

**Measuring — and then reducing — where AI coding agents actually spend tokens.**

> **Program result (2026-08; corrected 2026-10-05 after an independent audit):** on live, graded
> coding sessions (one repo, one model, one client version, one machine), Claude Code run with
> **admission control** (`--disallowedTools`) plus the gateway's **context-lifetime management**
> cut cumulative input tokens **41.5%**, with task quality passing the preregistered
> non-inferiority rule exactly at its bound (B6, 24 sessions, preregistered) — but dollars fell
> only 2.5%, and admission alone predicts about as much token reduction (−44.0%), so the lifetime
> mechanisms' live share is unidentified. Admission alone (a client flag; the proxy applied no
> mutations) cut list-price dollars **29.3%** (B8 v2, 3 pairs, per pair −5.9% to −44.9%); its
> −29.5% prediction was first committed together with the results, so B8 v2 is a **post-hoc
> check, not a preregistered test**. A cache-cost replay puts the giant-long-context regime at
> **−49.7% dollars pooled over 54 sessions, median 0% (modeled, not live)**.
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

### Research papers

The repository now includes two full, reproducible papers and a source-linked map of prior work:

- **[Tokens Are Multiplied by Turns](papers/dist/context-residency-measurement.pdf)** — measurement, opportunity ceilings, and the negative-result path that narrowed the design space.
- **[ContextRuntime](papers/dist/contextruntime-systems.pdf)** — admission, safe lifetime control, cache-aware scheduling, and staged B6/B7/B8 evaluation.
- **[Research landscape](papers/RESEARCH_LANDSCAPE.md)** — prompt compression, agent memory, code/tool retrieval, serving caches, cache economics, and the specific gap this project fills.
- **[Paper sources and reproduction guide](papers/README.md)** — LaTeX, bibliography, generated figures/tables, evidence grades, and exact build commands.

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

The package also ships the production-path runtime the B-series tested live:

- **`contextruntime/retirement.py`** — `RetirementPlanner → HistoryMutationPlan → HistoryMutator`
  (policy separated from mechanism; safe-by-construction retirement of superseded/cold tool
  results).
- **`contextruntime/gateway.py` + `gateway_proxy.py`** — a stdlib HTTP gateway
  (`python -m contextruntime.gateway_proxy`, point `ANTHROPIC_BASE_URL` at it) with modes
  `CR_GATEWAY_MODE=off|observe|enforce`, thinking-GC (`CR_GATEWAY_THINKING_KEEP`), and
  response-level fail-open (any upstream 4xx to a mutated body resends the original bytes —
  **0 `fallback_original` events in the 12 auditable B6 enforce sessions**, all with cache
  alignment off; the logger records only 4xx-on-mutated outcomes). The proxy does **no
  admission**: admission is the client's own `--disallowedTools` flag.
- **`contextruntime/cachemodel.py` + `cachealign.py`** — the prefix-cache cost model (validated
  only on its append-only branch: exact on 11/12 live B6 native sessions) and the
  cache-aligned scheduler
  (`CR_GATEWAY_CACHE_ALIGN=off|cold|gated`): fired mutations become persistent/byte-stable;
  new mutations fire only when the cache is cold or a break-even rule clears (as shipped, that
  break-even branch cannot fire on the default `anthropic-1h` profile — see
  [Known limitations](#known-limitations--open-questions-audit-2026-10-05)).
- **`contextruntime/prefixdoctor.py`** — `cr doctor --prefix`: zero-quota capture + per-item
  audit of the fixed prefix (what to KEEP/DEFER/DISABLE, with feasibility tags).
- **`contextruntime/providers.py`** — the framework is **provider-generic**: every algorithm
  reduces to four constants (`read_mult`, `write_mult`, `ttl_s`, `out_mult`), selected by
  `CR_GATEWAY_PROFILE`. One derived number — break-even reads per rewritten token — flips the
  scheduler's verdict between providers (Anthropic-1h: 19, hold on short sessions; free-write
  providers: 1, fire almost always). Cross-provider sensitivity on the same real sessions (offline
  replay rule, not the shipped scheduler):
  **[docs/provider-profiles.md](docs/provider-profiles.md)** (only `anthropic-1h` has been checked
  against live sessions — append-only branch exact on 11/12 B6 sessions, plus the B8 v2 post-hoc
  check; the rest are calibration-pending presets).

Earlier phases (**[STATUS.md](docs/STATUS.md)**): 0b residency graph · 1 ContextReduce ·
2 SemanticFS/Graph-Lite — the graph-retrieval line was **closed by measurement** (G1/G2, B5):
the measured live win is admission, not out-searching the model (lifetime control's live
contribution is not yet identified).

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

Each row below carries its own evidence grade (live or modeled; preregistered or post-hoc;
graded or not). The live rows come from real Claude Code sessions in a single environment — one
repo (django, SWE-bench-Verified tasks), one model (Sonnet), one client version (2.1.229), one
machine; 4 tasks in B6, 3 in B8 — graded with a local macOS runner, not the official SWE-bench
harness. Treat them as design-partner evidence pending replication. Full write-ups in
`docs/b*-findings.md` (B6–B8 carry dated audit corrections); frozen artifacts and per-session
gateway logs in `corpus/analysis/`.

| claim | number | evidence grade |
|---|---|---|
| End-to-end **context-workload** reduction: admission (`--disallowedTools`) + gateway retirement + thinking-GC; quality passed the non-inferiority rule at its bound (9 vs 10 of 12 graded successes; django-16502 T 0/3 vs N 2/3) | **−41.5%** pooled input tokens (per-task −28…−59%); dollars −2.5% (CLI) / −2.75% (list price, 1h writes). Admission-only arithmetic predicts −44.0%, so retirement/thinking-GC's live share is unidentified | **Live, preregistered, graded** — B6, 24 sessions |
| **Live dollar** reduction from client-side admission (25 built-in tools + 3 MCP servers disallowed); the gateway ran in the T arm but applied no mutations; quality 9/9 vs 9/9 on 3 tasks chosen after B6 (excluding django-16502) | **−29.3%** list price (pairs −5.9 / −31.0 / −44.9%); the CLI's own estimate from the same usage counts: −29.2% | **Live, graded, not preregistered** — B8 v2, 3 pairs; the −29.5% prediction first appears in git with the results (post-hoc check) |
| Cache-cost model (1h-tier pricing, partial interior hits, extent semantics) | append-only branch exact on 11/12 B6 native sessions (12th +103.7%); the edit branch's 7.3% median error has no committed artifact; B8 v2 agreement −0.16 pp (post-hoc) | **Append-only branch only** — never validated on the edit branch or a live break-even fire |
| Mutation safety: rejected mutated requests; retirement-caused re-reads | 0 `fallback_original` in the 12 auditable B6 sessions (alignment off); the 3 B8 v2 sessions applied no mutations; the 2 B8 v1 sessions have aggregates only. The logger sees only 4xx-on-mutated (40 of 71 requests in one B6 session have no recorded outcome). The preregistered re-read endpoint was never computed (11 vs 12 are unconditional same-path repeat reads) | **Live, partial** — B6 (+B8) |
| Giant-long-context regime (retirement + thinking dollars) | **−49.7%** pooled over all 54 replayed sessions (median 0%); −61.5% on the 36 passing the calibration filter, which are 28 headless runs from this project (0.0% change) + 8 interactive sessions, 3 of which carry 76.5% of the saving; −24.0% at a ≤5% filter. Replay gating rule, not the shipped scheduler | **Modeled** — B7 replay; not live |

**Working thesis:** `token efficiency ≈ admission control + lifetime control` — control *what
enters* the prefix and *how long it stays*. Live, only admission has a measured dollar effect;
lifetime control's live contribution is not yet identified. Retrieval sophistication (code
graphs, discovery-packet substitution) was measured and closed: enforced live, eager discovery
packets made sessions use **+71.6% more input tokens** (mean; dollars +79.7% mean / +66.9%
pooled, censored by the $2.50 per-session cap) (B5.3).

**Platform facts discovered en route** (each independently useful):

1. This client requests the **1-hour prompt-cache TTL — cache writes bill at 2.0×** base input,
   which is why naive history mutation saves tokens but not dollars (B6's −41.5% tokens was only
   −2.5% dollars; scheduling has not been shown to recover those dollars live — B8 v2's dollar
   saving came from admission, with no mutations applied).
2. **A custom `ANTHROPIC_BASE_URL` disables MCP tool-schema deferral** — any gateway deployment
   silently starts ~43k tokens/request behind the native client (first request 84,676 vs 41,554
   tokens). **Admission is not an optional lever in a gateway product; it is the entry fee** (B8v1).
3. The prompt cache serves **partial interior hits**, and the 1h TTL is **soft** (65-minute idle
   gaps did not expire it live).
4. Claude Code stores one API call as several transcript records sharing a `requestId` — usage
   analysis must merge them (`corpus/transcript_util.merged_records`) or per-turn numbers
   inflate ~1.9×.

## Run it — quickstart

**Step-by-step usage guide: [docs/USAGE.md](docs/USAGE.md)** (requirements, both paths, every
env var, safety properties, how to confirm it's working, current limitations).

Two ways to use it with Claude Code today:

```bash
# Path 1 — no gateway, any plan: audit the fixed prefix (zero tokens billed) and fix your config
python3 -m contextruntime.cli doctor --prefix --cwd /path/to/project --sessions 30

# Path 2 — the gateway: OBSERVE first (log-only), then ENFORCE
CR_GATEWAY_MODE=observe CR_GATEWAY_LOG=$HOME/cr-gateway.jsonl python3 -m contextruntime.gateway_proxy
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude --disallowedTools <never-used tools from the doctor> ...
CR_GATEWAY_MODE=enforce CR_GATEWAY_THINKING_KEEP=1 CR_GATEWAY_CACHE_ALIGN=gated python3 -m contextruntime.gateway_proxy
```

**Provider support (honest):** Claude Code → Anthropic API is supported and live-tested (see
[Known limitations](#known-limitations--open-questions-audit-2026-10-05)). Any
client speaking the Anthropic Messages format should work but is unvalidated. **GPT/OpenAI, Gemini,
and local vLLM/Ollama models are not supported at runtime yet** — the gateway parses Anthropic
message shapes only; those providers exist as cost-model presets for offline replay
(`docs/provider-profiles.md`) and need an OpenAI-format adapter plus a calibration pass.

Legacy batch profilers (Phase 0, reference):

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

**B-series complete and frozen** (B1–B8; research lines B1/B2/G1/G2/B5 closed by measurement;
the B6–B8 write-ups carry dated audit corrections). Live: 41.5% context workload with −2.5%
dollars (B6, preregistered) · 29.3% dollars from client-side admission (B8 v2, post-hoc check).
Not demonstrated live: any dollar contribution from retirement, thinking-GC or the scheduler
("scheduler no-harm" is vacuous: its break-even branch cannot fire on `anthropic-1h`, and B8 v2
applied no mutations); mutation safety rests on the 12 auditable B6 sessions. Modeled only: the
giant-session regime (B7: −49.7% over all 54 sessions, median 0%). Next: the open experiment
below. The experiment log, in order:
`docs/b3-findings.md` → `b3.1/b3.2` → `B3_DECISION.md` → `path-to-50.md` → `prefix-doctor-findings.md`
→ `call-collapse-findings.md` → `joint-stack-findings.md` → `executor-ab-findings.md` →
`b6-protocol.md`/`b6-findings.md` → `b7-findings.md` → `b8-protocol.md`/`b8-findings.md`.

### Known limitations / open questions (audit, 2026-10-05)

- **Scheduler.** On `anthropic-1h` (and `anthropic-5m`) the shipped break-even branch cannot
  fire: it needs `0.1·P·E ≥ (w−0.1)·S` with E = 8, but the suffix S is counted from the earliest
  pending tool result (`gateway._suffix_tokens_est`), so S ≥ P. Only cold-start and ttl-gap fires
  happen. B7's modeled "gated" savings come from the replay rule in `corpus/b7_cache_replay.py`,
  not the shipped scheduler. Documented, not yet fixed.
- **Task selection.** B8's tasks (16485, 16527, 16901) were fixed after B6's results and exclude
  django-16502, the only task where B6 treatment failed (T 0/3 vs N 2/3).
- **Known gateway bugs (unfixed).** Persistent retirements are forwarded upstream only when the
  same request also fired a new retirement or stripped thinking (`gateway_proxy.py:47`), yet
  `persistent_applied` is still logged; a fired mutation the API rejects is retried on every later
  request (no rollback); `doctor --prefix --no-capture` crashes; the doctor's same-project session
  filter misses project paths that contain `_` (e.g. in the username).
- **Generalization.** One repo (django), one model (Sonnet), one client version (2.1.229), one
  machine; 4 tasks in B6, 3 in B8.
- **Missing comparator.** The native arm is stock Claude Code with its default MCP-schema deferral,
  so admission was measured against that; but no arm runs disallow-only without the proxy, and none
  compares admission with the client's own tool search / deferred loading beyond its defaults.
- **Open experiment.** Stock client with tool search enabled vs disallow-only (no proxy) vs
  the full gateway, on more tasks and repos including django-16502, graded with the official
  SWE-bench harness.

---

*Engineering posture: version-gated, inferred, best-effort, fail-open, or
verify-at-runtime where the platform doesn't guarantee behavior. The design doesn't
claim to control things it can't.*
