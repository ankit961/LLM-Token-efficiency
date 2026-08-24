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

## B5.2R — rerun with `alwaysLoad: true` (2026-08-24, ~$5.30)

The three B-arms were rerun with `"alwaysLoad": true` on the `cr` server (A baselines reused;
per-pair records `corpus/analysis/executor-ab-v2.json`):

| task | A calls / Σ input | B(R) calls / Σ input | discover | native disc A→B | same src files |
|---|---|---|---:|---|:--:|
| 10554 | 52 / 3.82M | 45 / 3.76M (−13.5% / −1.6%) | 4 | 31→21 | ✅ |
| 11138 | 49 / 3.30M | 53 / 4.40M (+8.2% / +33.1% MORE) | 1 | 21→26 | ✅ |
| 14608 | 37 / 2.05M | 36 / 2.22M (−2.7% / +8.1% more) | 1 | 16→15 | ✅ |

**Delivery is FIXED: 3/3 adoption, all success, same source files.** The subscription client CAN carry
the executor — `alwaysLoad` resolves the race exactly as documented, and the earlier "requires a
custom loop" conclusion is withdrawn.

**Displacement is NOT achieved: the efficiency gates fail** (mean calls reduced only 2.7% vs the ≥10%
gate; mean Σ input **~13.2% MORE** than A vs the ≥8%-reduction gate — sign stated plainly per review). Adoption was *shallow* — 1–4 discover calls per session used as an
*additional* search tool while the native discovery chains continued (21–26 native calls after the
first discover). The packets add cache-creation tokens (43–109k) without replacing the runs they were
built to collapse.

**Where this leaves the executor:** the blocker has moved from *delivery* (solved) to *behavior* —
getting the model to **substitute** discover for its discovery runs rather than add it. The
next candidates, in escalating force: protocol-level steering, disallowing native search tools in the
B-arm (distorts non-inferiority, previously rejected), or the custom loop where the tool surface and
the discovery policy are ours. n=3 × 1 rep; same-task rep variance reaches 1.9× (Step 7), so per-task
deltas are within noise — the consistent signal is the *absence* of the displacement effect, not a
reliable cost increase. STOP per directive.

## B5.3 — ENFORCED substitution (2026-08-24, ~$7.50): call-collapse CLOSES

Arm C = `alwaysLoad` discover + policy prompt + `--disallowedTools` for Grep/Glob and exploratory
shell search (Read preserved for targeted follow-up and edit preconditions). A baselines reused.
Per-pair records: `corpus/analysis/executor-ab-v3.json`.

| preregistered gate | result |
|---|---|
| adoption = 100% | **PASS** — 3/3, 15–16 discover calls each |
| displacement ≥ 70% | FAIL — mean 65.1% **over the enforced categories** (68.4 / 76.9 / 50.0; A explore 19/13/10 → C 6/3/5; Read excluded by design — it stayed allowed; reads A 12/8/6 vs C 9/9/8) |
| API calls −10% | FAIL — mean −0.5% (−19.2 ✓ / −14.3 ✓ / **+35.1% more**) |
| Σ input −8% | **FAIL decisively — mean +71.6% MORE input** (+11.5 / +39.5 / +163.8; pooled +55.6%). Authoritative source = transcript per-call sums (A 3.82/3.30/2.05M → C 4.27/4.61/5.42M); the runner's modelUsage sums (4.22/4.61/5.20M, mean +67.7%) are stored as reference — either source fails the gate by a wide margin. Formulas + denominators: `executor-ab-v3.json → authoritative_measurements` |
| non-inferior | marginal fail — same source files 2/3; one budget cap; fewer test runs |

**The mechanism, finally visible:** under enforcement the model uses discover **as a search engine,
iteratively** — 15–16 calls per session, *more* than its native search count in A. Every call returns
a multi-kilotoken packet that stays resident for the rest of the session. The oracle's premise — ONE
packet replaces a RUN — did not transfer: **in all three enforced runs the model continued stepwise
exploration despite the consolidated discovery tool** (three django tasks and one agent — enough to
close this preregistered branch, not a general law about all coding models). Enforcement solved
*adoption*; it did not change exploration behaviour here.

**Decision (preregistered), scoped precisely: CLOSE eager multi-kilotoken discovery packets as a
primary token-saving mechanism.** Not the stronger claim that no local discovery system could ever
save tokens — what is established is that, on these tasks and this agent, forcing the model through a
richer eager retrieval tool does not change its iterative exploration enough to save tokens and can
make residency dramatically worse. No embeddings, no graph sophistication, no D2 planning. The 13.1% oracle stands as an offline opportunity bound;
realizing it would need packet economics fundamentally different from eager top-k fetching
(incremental/cursor packets, or packets that retire on supersession) — noted, not pursued.

**The stack survives the closure:** after removing call-collapse entirely, the admission + lifetime
counterfactual still yields **roughly 52–55% opportunity on the two gateway environments and roughly
39–49% on the subscription/configuration environments** (49.2 / 52.3 / 39.4 / 54.9% for lean-sub /
lean-gw / heavy-sub / heavy-gw). Removing collapse leaves more calls over which lifetime management
acts, so the attribution shifts between levers; the joint numbers are what the replay establishes. The
~50% gateway engineering target stands on **admission (prefix) + lifetime (B3, thinking-GC)** alone.

Arc complete: voluntary → shallow adoption; delivery-fixed → additional-tool use; enforced → deep
adoption with anti-economical packets. Three experiments, one clean scoped closure, and the program's
thesis sharpens: *control what enters and how long it stays; do not spend the product's complexity
budget trying to change how the model searches.*
