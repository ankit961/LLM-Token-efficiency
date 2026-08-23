# B5.0 — `cr doctor --prefix` v1: findings and the hard decision gate

**2026-08-23. Zero model quota** (captures answered locally; nothing forwarded). Frozen artifact:
`corpus/analysis/prefix-doctor-v1.json` (both environments, per-item tables, counterfactuals).
Module: `contextruntime/prefixdoctor.py`; `contextruntime doctor --prefix [--capture-model M]`.
Diagnostic only — the doctor never disables or rewrites anything.

## Method (and its honesty guarantees)

- **Capture, zero quota**: a local proxy records the main `/v1/messages` body `claude -p` sends and
  answers a non-retryable 400. Auth headers never stored; `stdin=DEVNULL` (a non-TTY stdin is appended
  to the prompt — found the hard way).
- **Attribution reconciles, residual stated**: every tool schema and system/injected block is
  tokenized (cl100k) and reconciled against the REAL first-call prefix from same-environment sessions.
  the **Claude request-accounting factor is ~1.74× the cl100k estimate** on coding-agent content
  (455 clean call-deltas, IQR 1.61–1.89). Stated as an accounting factor, not a tokenizer claim: it may
  include request serialization/protocol/invisible content; pinning it to vocabulary alone needs the
  official count-tokens endpoint on identical bytes. Practical consequence stands: heuristic/tiktoken
  estimates understate Claude-billed usage by ~40%. Residuals: heavy **−9.5%**, lean **−8.1%** — attribution and reality agree within the
  tokenizer spread.
- **Deferral-aware**: whether a schema is *resident* is observed, not assumed — from the capture
  (loaded `tools` array) and from transcripts (`deferred_tools_delta` = names only, NOT resident). The
  Aug-18 lean sessions deferred **74** tools; today's environment defers **none** (all 82 loaded).
  Deferral state is environment/version-dependent and must be measured per environment.
- **Model-dependent prefix**: sonnet's core prompt is 6,079 cl100k vs opus's 2,324, with larger
  Bash/Agent descriptions — capture with the model your sessions use (`--capture-model`).

## The two environments

| | HEAVY (this machine, desktop + connectors) | LEAN (django Step-7 headless) |
|---|---|---|
| real startup prefix | **82,359** | **42,995** |
| median calls/session | 49 | 46 |
| fixed prefix share of Σ P_t | 71.9% | 67.8% |
| tool schemas | 72,482 (88%) | 28,324 (66%) |
| core prompt (Claude Code's own) | ~9,725 | ~9,849 |
| **unused tool schemas** | **38,466 (47%)** | **20,140 (47%)** |

The headline structural fact, identical in both environments: **~47% of the startup prefix is schemas
for tools that are never invoked.** In the heavy environment that includes an idle Gmail connector
(28 tools, ~19k) plus never-used builtins (DesignSync, Cron*, EnterWorktree, RemoteTrigger…); in the
lean headless environment it is Workflow (8.2k), Artifact (6.1k), Agent (3.1k), ReportFindings, Skill —
desktop-oriented tools loaded into headless coding runs that never touch them.

## Counterfactual waterfall (replayed over observed call counts — opportunities, NOT realized savings)

| config | HEAVY: prefix / Σ P_t | LEAN: prefix / Σ P_t | feasibility |
|---|---|---|---|
| P1 unused MCP servers removed | −23.1% / **−16.6%** | −1.0% / −0.7% | SUBSCRIPTION_CONFIG |
| P1b + never-used schemas disallowed | −38.4% / **−27.6%** | −45.1% / **−30.6%** | SUBSCRIPTION_CONFIG (validated: `--disallowedTools` strips the definition) |
| P2 + late-used tools deferred to first use | −63.0% / **−45.3%** | −49.2% / −33.4% | ANTHROPIC_CLIENT_REQUIRED (Tool Search; custom loop can use the API feature today) |
| P3 + static instructions compressed 50% | −63.0% / −45.3% | −52.4% / **−35.5%** | mixed |
| P4 oracle (everything at first use, core kept) | −81.2% / −58.4% | −58.1% / −39.4% | mixed |
| **subscription-achievable today** | **−38.4% / −27.6%** | **−47.0% / −31.9%** | SUBSCRIPTION_CONFIG |
| **gateway/custom-loop-achievable** | **−63.0% / −45.3%** | **−52.4% / −35.5%** | GATEWAY_CONTROLLABLE |

Core floor ≈ **9.9k tokens** (Claude Code's own prompt + client-injected) — the non-negotiable bottom.

## The hard gate — answers A–F

- **A. Truly controllable startup tokens:** heavy **36,750 / 82,359 (45%)**; lean **24,222 / 42,995 (56%)**.
- **B. Unused throughout a typical task:** heavy **38,466 (47%)**; lean **20,140 (47%)**.
- **C. Deferrable until first use** (tokens × fraction of calls before first use, including used
  tools): heavy **66,840/call-equivalent (81%)**; lean **22,558 (52%)**.
- **D. % of whole-session Σ P_t under P1 / P2 / P3:** heavy **16.6 / 45.3 / 45.3**; lean **0.7 / 33.4
  / 35.5** (the lean env has no removable MCP server, so its first big step is P1b: **30.6**).
- **E. Possible on Claude Code subscription today:** heavy **−27.6%** Σ P_t; lean **−31.9%** Σ P_t —
  disconnect idle connectors + `--disallowedTools`/`permissions.deny` for never-used schemas (both
  validated to strip definitions from the request) + trimming user-owned listings.
- **F. Requires our gateway / custom loop:** the increment from E to **−45.3%** (heavy) / **−35.5%**
  (lean) — deferral-at-first-use via the API's Tool Search / `defer_loading`, which Claude Code does
  not expose yet (issue #12836) but a custom loop can use today.

**Gate verdict (rubric: <5 no / 5–15 secondary / 15–30 major / >30 primary wedge):** realistic
subscription-achievable reduction is **~28–32% of whole-session Σ P_t** — at the top of "major lever",
crossing into "**primary product wedge**" on headless workloads. This is now the largest single lever
in the program, ahead of B3 (8.3%), thinking-GC (≤11.3%) and everything else measured.

## Multiplicative stack (correction adopted from review)

Levers overlap multiplicatively, not additively. With measured values — prefix −30%, call-collapse
−15% (unvalidated bound 37.5%), B3 −8%, thinking-GC −7%:

    0.70 × 0.85 × 0.92 × 0.93 ≈ 0.509  →  ~49% total reduction

So **50% is credible only as prefix + call-collapse + B3 + thinking-GC together**, and the two
multipliers carry it. `docs/path-to-50.md` §5 carries this correction.

## Caveats

- **Counterfactual, not realized.** No live A/B was run; percentages are replays of observed call
  counts over reduced prefixes.
- **"Unused" is recency-bounded** (60 sessions per environment) and environment-specific: Workflow /
  Artifact are DEFER (used, late) on the interactive machine but DISABLE? in headless runs. The doctor
  recommends; a human decides.
- **Lean attribution is partly reference-based** (tool sizes + core prompt from this machine's capture
  at the same Claude Code version/model; listings measured from its own transcripts) — flagged in the
  report, residual −8.1%.
- **Cost ≠ tokens**: cache reads are 0.1×; on these sessions cache-read is ~61% of dollar cost, so a
  −30% Σ P_t is roughly −20% of cost (plus a smaller write saving). The context-window/residency
  benefit is the full token percentage.
- P2's deferral saving assumes first-use timing stays as observed; deferring a schema could in
  principle change model behaviour (it no longer *sees* the tool early) — same class of risk Tool
  Search accepts, and Anthropic reports accuracy *improved* with it, but it is not free of behaviour
  change.

## STOP

Per the directive: no automatic disabling, no live A/B, no prefix mutation built. The decision this
gate feeds: whether prefix hygiene becomes the primary product wedge (doctor → recommended config →
optional gateway deferral), and whether the next measured multiplier is call-collapse or thinking-GC.
