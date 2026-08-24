# B5.2 Stage B — Live discovery-executor A/B: NOT REALIZED (a delivery failure, precisely measured)

> **Correction (2026-08-24, per review): the "requires a custom loop" conclusion below was premature.**
> Claude Code provides exactly the control this experiment needed and did not use: `"alwaysLoad": true`
> on an MCP server loads all its tools at session start and **blocks startup until the server
> connects** (and `MCP_CONNECTION_NONBLOCKING=0` restores blocking startup generally). The correct
> conclusion is: *B5.2B failed because the tool was not present on turn 1, and the eager-loading
> mechanism went untested.* A zero-quota capture confirms the discover schema CAN be present in the
> first request (240 cl100k tokens; note the capture's timing cannot reproduce the lost race, so the
> live rerun is the discriminating test). **B5.2R** reruns the three B-arms with `alwaysLoad: true`;
> results in `corpus/analysis/executor-ab-v2.json` and the B5.2R section of this document.

**2026-08-24. Live; ~$10 of quota (user-approved).** Runner `corpus/executor_live_ab.py`; executor
`contextruntime/discover.py` + `contextruntime/discover_mcp.py`; frozen artifact
`corpus/analysis/executor-ab-v1.json` (per-pair records). Paired A/B on 3 django tasks (sonnet, $2.50
caps): **A** native `claude -p`, **B** identical + the `discover` MCP server + steering system prompt.
Native tools stayed available; fallback was a measurement.

## The result in one line

**The executor was never exercised: 0 `discover` calls across all 3 B-arms.** The hard gates
(calls −10%, Σ input −8%, non-inferior success) are therefore **not meaningfully evaluable** — the
experiment did not test the oracle's claim; it uncovered a delivery blocker in front of it.

| task | A calls / Σ input | B calls / Σ input | B status | discover calls |
|---|---|---|---|---:|
| 10554 | 52 / 3.82M (success) | 62 / 4.70M (**budget cap**) | worse | 0 |
| 11138 | 49 / 3.30M (success) | 38 / 2.72M (**700s timeout, unfinished**) | truncated | 0 |
| 14608 | 37 / 2.05M (success) | 29 / 1.52M (success) | variance | 0 |

The paired deltas (−23.0%, +17.7% timeout-truncated, +26.2%) are trajectory variance around an arm
whose only real difference was an unused MCP server and a steering paragraph.

## Root cause — measured, not guessed

1. **A startup race turns our tool invisible.** `claude -p` fires its first API call while the `cr`
   MCP server is still `pendingMcpServers`; when the server connects (~1s later), its tool arrives as
   a **names-only `deferred_tools_delta`** — the schema is never in context. Observed in **3/3**
   B-arms. (Zero-quota capture confirms: when the request goes out *after* the server is up,
   `mcp__cr__discover` is fully loaded.)
2. **Steering cannot reach an invisible tool.** The system prompt explicitly said to call
   `WaitForMcpServers` or ToolSearch first if discover was missing; the model did **neither, 0/3**.
   A deferred name buried among ~75 others, with no schema, loses to familiar native tools every time.
3. This is the **SemanticFS adoption lesson with a sharper mechanism**: last time (0/11) we could not
   distinguish "model prefers native tools" from "tool effectively invisible"; this time the
   invisibility is directly observed in the transcripts.

## What this does and does not say

- It does **not** refute the collapse oracle (13.1% of calls, retention-gated): the executor's
  packets were never used, so their live effect is untested either way.
- It **does** extend the doctor's subscription-vs-gateway boundary to a new surface: on the
  subscription client, not only tool *schemas* but tool **delivery** (deferral, server-startup
  ordering, load-on-demand behavior) is client-controlled. **Prompt steering alone cannot overcome a
  schema the model never sees.**
- Realizing call-collapse therefore needs one of: a **custom loop** (we own the tool list — the same
  place the gateway stack already wins), **client-side support** (non-deferrable MCP tools / server
  readiness before the first call — a Claude Code feature request), or forcing adoption by
  disallowing native discovery tools (rejected here: it distorts the non-inferiority comparison).

## Honest accounting

- Spend ≈ $9.7 (incl. one budget-capped and one timed-out B-arm). n=3 tasks × 1 rep.
- Runner bugs found and fixed en route (kept in the repo): `modelUsage` must be summed across model
  entries; worktree→transcript mapping encodes both `/` and `_` as `-` (the Step-7 lesson,
  re-learned); graceful timeout + incremental saves + resume.
- 11138-B "improvement" is an artifact of truncation; 14608-B's is variance (same-task rep variance
  was measured at up to 1.9× in Step 7).

## Per the directive: STOP

No D2 conversion, no semantic planner, no further spend. The B5.2 pair of results stands as:
**Stage A (exact joint replay): the counterfactual stack is real and larger than the multiplicative
estimate (40.3–57.2%). Stage B: realizing the call-collapse slice live is blocked at tool delivery on
the subscription client — it belongs to the custom-loop/gateway product path, alongside the other
gateway-only levers.**
