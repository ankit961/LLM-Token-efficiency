# `cr doctor --prefix` — itemize the fixed prefix re-read on every API call

> **v1 (2026-08-23):** the doctor now reconciles attribution against the real first call (residuals
> stated, ~±10%), is deferral-aware (schemas listed in `deferred_tools_delta` are NOT charged),
> audits first-use timing (wasted residency = tokens × calls before first use), emits conservative
> KEEP/DEFER/DISABLE?/COMPRESS/UNKNOWN actions tagged SUBSCRIPTION_CONFIG / GATEWAY_CONTROLLABLE /
> ANTHROPIC_CLIENT_REQUIRED, and computes the P0–P4 counterfactual waterfall for both environments.
> Findings + the hard decision gate: `docs/prefix-doctor-findings.md`; frozen artifact:
> `corpus/analysis/prefix-doctor-v1.json`. The v0 report below stands as the first diagnosis.

**2026-08-23. Built, tested, run on a real machine. Zero model quota.** `contextruntime/prefixdoctor.py`,
`contextruntime doctor --prefix [--cwd DIR] [--sessions N] [--json]`; this machine's report in
`corpus/analysis/prefix-doctor-report.json`.

## Why this is the first build after `docs/path-to-50.md`

The full prefix decomposition showed that the **fixed prefix** — system prompt + tool definitions +
injected context — is **~73% of all resident token-turns** on lean sessions and **~99%** in heavy-MCP
environments, and it is **mostly configuration**. Nothing else in the program is that large, and
nothing else is that cheap to change. The doctor turns the 73% into a per-item bill with usage
evidence and exact fixes.

## How it works

1. **Capture (zero quota).** It starts a local capture proxy, runs `claude -p` with
   `ANTHROPIC_BASE_URL` pointed at it, records the **main** `/v1/messages` body (Claude Code fires small
   auxiliary calls first; the main agent request is the one carrying `tools`), and answers with a
   non-retryable 400. **The request never reaches Anthropic; auth headers are never stored**; the
   captured body (which contains your system prompt, CLAUDE.md and memory) stays in memory unless you
   pass `--json`. Takes ~5 s.
2. **Itemize.** Token-count every tool definition (grouped by MCP server / builtin) and every system
   and injected block (CLAUDE.md, memory, skills, agents, environment, core). Heuristic counts are
   **calibrated to the real `cache_creation_input_tokens` of the first call** of recent sessions from the
   same project — JSON schemas tokenize ~1.8× denser than the heuristic assumes, so uncalibrated
   numbers understate tool cost badly.
3. **Evidence + advice.** Scan recent transcripts (requestId-merged, real calls) for which MCP servers,
   builtin tools, skills and agents were *actually invoked*, plus median calls/session and the fixed
   prefix's share of resident token-turns; recommend disconnecting unused MCP servers, `--disallowedTools`
   for heavy unused builtins, and trimming oversized always-resident blocks — each sized in tokens per
   call and token-turns per session.

Validated (zero quota) that the recommendations are *real*: `--disallowedTools` strips the named
definitions from the request (Workflow + Artifact alone: −9.6k heuristic ≈ **−17k real tokens/call**);
`--disallowedTools "mcp__<server>__*"` removes a whole server's tools; `--strict-mcp-config` removes all
MCP servers (here −34% of the prefix).

## This machine's report (the 82k-startup environment from the OBSERVE run)

```
startup prefix: ~82,365 tokens, of which 82 tool definitions = ~77,405   (calibrated; median recent 41,894)
recent sessions: 60 scanned; median 53 API calls/session; fixed prefix = 66.5% of resident token-turns

component                      tokens/call  used (recent)
tool:builtin                        49,320          10749
tool:claude_ai_Gmail                22,200              0
tool:mobile                          5,885              4
system                               4,781
injected                               166

heaviest tool definitions                  tokens/call   uses
Workflow                                         9,675     32
Artifact                                         7,554     36
DesignSync                                       4,164      0
Monitor                                          3,520     20
mcp__claude_ai_Gmail__search_threads             2,584      0
...

RECOMMENDATIONS — potential −36,253 tokens on EVERY call (44.0% of the startup prefix):
 - −22,199/call  disconnect MCP server 'claude_ai_Gmail' … or --disallowedTools "mcp__claude_ai_Gmail__*"  [28 tools, 0 uses in 60 sessions]
 - − 4,718/call  trim system[2]:claude_md                                                             [large always-resident block]
 - − 4,164/call  --disallowedTools DesignSync                                                         [0 uses in 60 sessions]
 - − 1,824/call  --disallowedTools EnterWorktree   · − 1,698 RemoteTrigger   · − 1,650 CronCreate         [0 uses]
```

Three things this says:

- **Tool definitions are 94% of the startup prefix** (77k of 82k). Anthropic's published "~77k tokens
  of tool definitions before work begins" is reproduced here almost to the token, on a real machine.
- **One idle connector is 27% of every call.** The claude.ai Gmail connector injects 28 tool schemas
  (22k tokens) into a *coding* project where it has never been used. At a median 53 calls/session that
  is ~1.2M resident token-turns per session for nothing.
- **Two built-ins — Workflow and Artifact — are 17k tokens (21%)** of every call. They *are* used here,
  so the doctor does not flag them; on a machine that never uses them, `--disallowedTools Workflow
  Artifact` is a one-line −21%.

Applying the evidence-backed recommendations alone cuts this environment's prefix by **44%**, i.e.
roughly **−30% of all resident token-turns** on a typical session — more than B3, B1 and every
tool-output mechanism combined, with zero runtime machinery and zero semantic risk.

## Limits (honest)

- **Subscription client, headless**: `--disallowedTools` / `--strict-mcp-config` apply to `claude -p`
  and settings `permissions.deny`; interactive desktop sessions inherit whatever the app injects, so
  the lever there is disconnecting connectors/plugins, not flags.
- **The core is not ours.** The Claude Code system prompt and the built-in tools you *do* use remain;
  the lean floor observed across this user's projects is ~42k. Going below that needs Anthropic-side
  features (Tool Search / `defer_loading` in Claude Code, issue #12836) or the gateway/custom-loop
  path where we control the prompt.
- **Usage evidence is recency-bounded** (default 40 sessions): a tool unused in the window may still be
  wanted; the doctor recommends, it does not change config.
- Heuristic tokens are calibrated per environment, not exact per item; relative sizes are reliable,
  absolute per-tool numbers are ±10–20%.
