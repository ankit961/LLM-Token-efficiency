# Using ContextRuntime — getting started

**Read this first — what works today:**

| your setup | status | what you get |
|---|---|---|
| **Claude Code** (any plan) → **Anthropic API** | **Supported & live-validated** | doctor audit + the gateway (admission + retirement + thinking-GC + cache-aware scheduler) |
| Your own agent loop → Anthropic Messages API | Should work (same request format); **not validated** | gateway; the doctor's capture step assumes the `claude` CLI |
| **GPT / OpenAI API**, Gemini | **NOT supported at runtime** — the gateway parses Anthropic message shapes only | cost-model presets for offline replay only (`docs/provider-profiles.md`); an OpenAI-format adapter is not built |
| Local models via vLLM / Ollama / SGLang | **NOT supported** (OpenAI-compatible format + no local pricing profile yet) | — |

Everything below is for the supported row. Numbers you should expect are in the README's results
table; the short version: **admission is the big, safe, always-on win (~−29% live dollars in the
gateway configuration); retirement/thinking-GC add residency savings and are dollar-neutral or
better only where the scheduler decides to fire** (long sessions, cold starts).

## Requirements

- Python ≥ 3.10; the runtime is **stdlib-only** (no installs). Tests: `python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest -q`.
- The `claude` CLI installed and logged in (for the doctor's zero-quota capture and, of course, as the agent).
- Nothing phones home: the proxy relays your client's own auth header upstream and never stores it; decision logs contain **counts and timestamps only, never prompt or tool content**.

## Path 1 — no gateway: audit your prefix and fix your config (any Claude Code plan)

The fixed prefix (tool schemas + system content) is re-billed on *every* call. In the environments
we measured, **~47% of it was schemas of tools that were never invoked.**

```bash
# from the repo root, in the project directory you want audited
python3 -m contextruntime.cli doctor --prefix --cwd /path/to/your/project --sessions 30
```

It runs one `claude -p` against a local capture endpoint (the request is captured and answered with
a non-retryable 400 — **zero tokens billed**), joins it with your recent local session transcripts,
and prints per item: tokens, first-use turn, wasted residency, and an action —
`KEEP / DEFER / DISABLE? / COMPRESS / UNKNOWN` — tagged by who can act on it
(`SUBSCRIPTION_CONFIG` = you, via settings; `GATEWAY_CONTROLLABLE`; `ANTHROPIC_CLIENT_REQUIRED`).
Add `--json` for machine-readable output; `--no-capture` to use transcripts only.

Act on the `SUBSCRIPTION_CONFIG` rows: disable MCP servers you never use, and/or pass the
never-used tools as `--disallowedTools` to `claude` (or deny them in `.claude/settings.json`).
That alone is the largest single lever in the program, and it needs no proxy.

The doctor is **diagnostic only** — it never changes your configuration.

## Path 2 — the gateway (Claude Code → local proxy → Anthropic)

### 1. Start the proxy in OBSERVE mode first (logs decisions, changes nothing)

```bash
CR_GATEWAY_MODE=observe CR_GATEWAY_LOG=$HOME/cr-gateway.jsonl CR_GATEWAY_PORT=8787 \
  python3 -m contextruntime.gateway_proxy
```

### 2. Point Claude Code at it — WITH admission

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
  claude --disallowedTools <the never-used tools from the doctor report> ...
```

**Do not skip `--disallowedTools`.** Measured fact: when `ANTHROPIC_BASE_URL` is a custom endpoint,
Claude Code disables its MCP tool-schema deferral and sends every schema on every request
(+43k tokens/request in our environment). Through a gateway, admission is not an optimization —
it is what gets you back to native parity before any saving begins.

### 3. Read the log, then switch to ENFORCE

Each request appends one JSON line: `turn`, `n_retirable`, `tokens_retirable` (what would be
freed), `thinking_strippable`, and in enforce mode `applied`, `thinking_stripped`, plus
scheduler fields (`fired`, `fire_reason`, `gap_s`, `pending_tokens`, `suffix_tokens_est`,
`persistent_applied`). A `response_usage` line follows each upstream reply. When the OBSERVE
numbers look sane for your workload:

```bash
CR_GATEWAY_MODE=enforce CR_GATEWAY_THINKING_KEEP=1 CR_GATEWAY_CACHE_ALIGN=gated \
CR_GATEWAY_LOG=$HOME/cr-gateway.jsonl python3 -m contextruntime.gateway_proxy
```

### Environment reference

| variable | values | meaning |
|---|---|---|
| `CR_GATEWAY_MODE` | `off` (default) · `observe` · `enforce` | kill-switch · log-only · mutate outbound history |
| `CR_GATEWAY_THINKING_KEEP` | integer ≥ 1 | thinking-GC: keep thinking only in the last N assistant messages (unset = off) |
| `CR_GATEWAY_CACHE_ALIGN` | `off` (default) · `cold` · `gated` | `off` = mutate at fixed batch boundaries (the B6 behavior); `cold` = new mutations only when the cache is cold (start / idle gap > TTL); `gated` = cold + break-even rule. Fired mutations persist (byte-stable) in both aligned modes |
| `CR_GATEWAY_PROFILE` | `anthropic-1h` (default, validated) · `anthropic-5m` · `openai-auto` · `gemini-implicit` | provider constants for the break-even rule; unknown names fall back to the default (strictest) |
| `CR_GATEWAY_LOG` | path | decision log (JSONL); unset = no log |
| `CR_GATEWAY_PORT` | integer (default 8787) | listen port on 127.0.0.1 |
| `CR_GATEWAY_UPSTREAM` | URL (default `https://api.anthropic.com`) | where requests are relayed |

### Safety properties (all live-tested)

- **Fail-open everywhere**: a parse error passes the request through untouched; an upstream 4xx to a
  *mutated* body resends the **original bytes verbatim** (logged as `fallback_original`). Across all
  live enforce sessions to date: 0 such events in 17/17.
- Retirement touches only provably-dead tool results (superseded by a later identical call, or
  untouched ≥ 5 turns) and replaces them with a stub carrying a recovery instruction. Measured
  re-read rate after retirement: unchanged vs native.
- Task quality was non-inferior in every graded live experiment (see `docs/b6-findings.md`,
  `docs/b8-findings.md`).

### How to confirm it is working

1. First request of a session: `cache_creation_input_tokens` should be roughly your *lean* prefix
   (ours: ~18k with admission vs ~85k without). If it's huge, admission isn't applied.
2. `fallback_original` lines should be absent.
3. Over a session, `persistent_applied` should be monotone non-decreasing once something fires,
   and `fire_reason` should be `hold` on most requests of a short, cache-hot session — that's the
   scheduler correctly *not* paying cache-write penalties.

### Current limitations

- **One proxy process per agent session.** Scheduler state (fired set, thinking frontier, last
  request time) is process-wide; running several concurrent conversations through one proxy mixes
  their gap detection and frontier. Start one proxy per session (different ports).
- The "1h" cache TTL is soft in practice (we observed no expiry at 65-minute gaps); `cold` mode's
  idle-gap trigger may fire on a still-warm cache. `gated` mode's break-even rule does not depend on it.
- Anthropic-ecosystem-specific levers (thinking-GC, the schema-deferral interaction) have no
  equivalent on other providers; **GPT/OpenAI and local models need an OpenAI-format adapter and a
  calibration pass before any of this applies** — see the porting ladder in `docs/provider-profiles.md`.

## Optional: the Phase-1 observation layer

`python3 -m contextruntime.cli install claude [--project DIR|--global] [--dry-run]` wires an
advisory, fail-open hook journal into Claude Code (uninstall with `uninstall`). It predates the
validated stack; its transparent-reduction path (`--enable-reduction`) measured ~0.03% live and was
closed — keep it off unless you are reproducing the early experiments.
