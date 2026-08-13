# ContextRuntime — Product Roadmap

*Evidence-anchored. Every phase gates on a measured number. Reordered from the
original PDF based on what the profilers actually found (2026-08).*

## Thesis

**Prefix size is the master lever.** Measured over 168 Claude Code sessions / 170k
requests: 96% of context occupancy is cache-reads (every resident token re-billed
every turn — the loop multiplier), and 82% of cache-writes are prefix rebuilds. The
one variable that cheapens BOTH the resident-read pool and the unavoidable
TTL-rebuild pool is how large the prefix is. The product controls what becomes
prefix, at what resolution, for how long — and ultimately stops carrying the
transcript altogether.

## Measured foundation (justifies the ordering)

| Finding | Value | Consequence |
|---|---|---|
| Cache reads / occupancy | 96% (72.56B) | loop multiplier is the real cost; token-turns is the unit |
| Cache-writes that are prefix rebuilds | 82% (1.53B geometric) | churn is first-order; but see avoidability |
| Avoidable churn (subscription mode) | ~24% (rest is TTL-idle) | admission can't PREVENT TTL breaks, only make them cheaper via size |
| Aged tool results (>50 turns resident) | ~23% of occupancy | eviction/working-set matters (gateway mode) |
| Strict duplicate re-delivery | 2.4% | dedup alone can't carry the product |
| Rewrite amplification (Claude Code) | 1.3× | output optimization is a non-problem here (edits are patches) |
| Model-switch churn | 2.7% | a warning feature, NOT a core pool |
| Occupancy concentration | top 20 sessions = 74% | per-workload-class reporting is mandatory |
| Unattributed occupancy | 39% | needs OTel/live capture (Phase 0b) |

## Product shape

- **Three modes:** Advisory MCP → Enforced adapter (PreToolUse gate + PostToolUse
  reduce, via hooks) → Full gateway (owns the request).
- **Two SKUs:** *Personal* (subscription wedge, Claude Code Max/Pro, no API key;
  market on limit-events + session-length, never simulated dollars) and
  *Enterprise control plane* (moat: cross-agent policy, org observability, shared
  deterministic index, security/ACL).

## Phases

- **0 — Instrumentation · DONE.** ContextScope + CacheScope + ModelSwitch.
  Gate passed with a twist: waste is real but RELOCATED to cache/prefix/eviction.
- **0b-A — Historical graph + ledgers · IMPLEMENTED.** Historical JSONL →
  residency graph (SQLite) → occupancy + economic ledgers. This is what the
  `contextruntime/` package does today.
- **0b-B — Live attribution · PENDING.** OTel + live hook capture to decompose the
  39% unattributed, find the mid-work-churn cause, and capture rate-limit % deltas.
  `doctor` is a **structured stub**, not a live capability probe yet. *Gate:* enough
  live attribution to act. **Not yet done.**
- **1-A — Reducer engine · IMPLEMENTED.** PostToolUse output reduction
  (tests/logs/grep/git), retention heuristic, `REDUCES` edges, handle resolution.
  Invariant tests pass; observe-mode measures ~53% on reducible tool results.
- **1-B — Causal task validation · PENDING.** The real Exp-B gate — *reduced vs raw
  preserves task success at material savings* — needs a same-task A/B agent trial.
  **Folds into the Phase-2 experiment.** Do not treat Phase 1 as gate-passed.
- **2 — Admission / prefix-size control (highest ceiling).** SemanticFS for ALL
  resources; PreToolUse graduated read gating; shell-parsing Bash policy.
  *Gate (Exp A, 3-arm: native/available/enforced):* the arm-2→arm-3 gap is the
  value of enforcement; confirm smaller prefix cuts TTL-rebuild + resident reads.
- **3 — Cache stability + scheduling.** Batch automated loops within 1h TTL (no
  keepalive), model-switch warnings, cache-island tracking. Meter-before-
  intervention safety net. *Gate:* recoverable share of avoidable-frequency churn.
- **4 — The capsule: "carry state, not transcript" (make-or-break).** Same model,
  same repo, ~2K capsule (intent + verified/hypothesis knowledge + repo refs) vs
  full transcript. *Gate (pivotal):* capsule preserves task success + time. Passes
  → earn cross-model, then cross-provider. Fails → state-portability dies here,
  cheaply.
- **5 — ContextPolicy.** observe→recommend→warn→soft→hard enforce. **Pull
  provenance/trust-level tagging forward** (user/source/test/tool/model-inference)
  — fixes injection + hypothesis-as-fact in the CURRENT single-agent product.
- **6 — Gateway mode.** Admission + lifecycle (eviction, history rewrite,
  compaction control). Unlocks the pools subscription mode can't reach.
- **7 — Cross-model / cross-provider capsule.** Codex (patchable OSS CLI), Cursor
  (Mode-1, coarse evidence). Report cohorts separately with evidence grades.
- **8 — Enterprise control plane.** Cross-agent policy normalizer, shared
  deterministic index, org observability, hard budgets, SSO/ACL, rate-limit
  failover. The business.
- **ContextGraph** — deferred to where retrieval-QUALITY is measurably the
  bottleneck (likely enterprise cross-repo). One subsystem, not the headline.

## Cross-cutting gates & metrics

- Progressive quality gates: G1 feasibility (30–50 tasks, ≥30% reduction, no
  catastrophic loss) → G2 product (100–200 curated) → G3 research (hundreds ×
  repeated, non-inferiority testing for the <2pp claim).
- Net token efficiency: retry-inclusive; count all recovery reads and test loops.
- Optimal working set C*(task, model): budget sweep 1k/2k/4k/8k/16k/adaptive × model.
- Baseline validity: own-harness-naive vs own-harness-semantic. Cross-harness =
  marketing, labeled.
- Calibration honesty: identify only the read-vs-rewrite differential; falsify
  token-linearity first; never claim to reverse-engineer the allowance formula.

## Do NOT build

Custom foundation/embedding models · Neo4j/graph DB · execution/DB/browser/notebook/
process state sync · a parallel repo-state tracker (reinvents `git status`) ·
the side-effect/idempotency ledger for non-idempotent ops (turns a profiler into
safety-critical infra — a company-defining pivot, not a feature).

## Biggest risk

Subscription-first has a structural ceiling: the 64% unavoidable-TTL and 72.56B
resident-read pools are only INDIRECTLY reachable in admission-only mode, via
prefix size. If Phase 2 shows admission can't shrink the prefix enough (model needs
the context), the value is all in Phase 6 gateway mode — harder to adopt. Phase 4
(the capsule) is the hedge. Phases 2 and 4 decide whether this is a Personal
product or Enterprise-only.
