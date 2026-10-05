# Using ContextRuntime — getting started

**Read this first — what works today:**

| your setup | status | what you get |
|---|---|---|
| **Claude Code** (any plan) → **Anthropic API** | **Supported & live-tested** (see [limitations](#current-limitations)) | doctor audit + client-side admission (`--disallowedTools`) + the gateway (retirement + thinking-GC + cache-aware scheduler) |
| Your own agent loop → Anthropic Messages API | Should work (same request format); **not validated** | gateway; the doctor's capture step assumes the `claude` CLI |
| **GPT / OpenAI API**, Gemini | **NOT supported at runtime** — the gateway parses Anthropic message shapes only | cost-model presets for offline replay only (`docs/provider-profiles.md`); an OpenAI-format adapter is not built |
| Local models via vLLM / Ollama / SGLang | **NOT supported** (OpenAI-compatible format + no local pricing profile yet) | — |

Everything below is for the supported row. Numbers you should expect are in the README's results
table; the short version: **admission is the big, always-on win — and it is a client flag
(`--disallowedTools`), not something the proxy does** (−29.3% live list-price dollars in B8 v2,
3 pairs of chained 3-task sessions, with the gateway applying no mutations). It removes
capabilities, so disallow only tools you never use. **Retirement/thinking-GC cut residency in B6,
but their live dollar effect is unmeasured** (B6: −41.5% tokens, −2.5% dollars), and on the
default `anthropic-1h` profile the `gated` scheduler only fires at a cold start or after an idle
gap longer than the TTL (see [Current limitations](#current-limitations)).

## Requirements

- Python ≥ 3.10; the runtime is **stdlib-only** (no installs). Tests: `python3 -m venv .venv && .venv/bin/pip install pytest && .venv/bin/python -m pytest -q`.
- The `claude` CLI installed and logged in (for the doctor's zero-quota capture and, of course, as the agent).
- Nothing phones home: the proxy relays your client's own auth header upstream and never stores it; decision logs contain **counts and timestamps only, never prompt or tool content**.

## Path 1 — no gateway: audit your prefix and fix your config (any Claude Code plan)

The fixed prefix (tool schemas + system content) is re-billed on *every* call. In the environments
we measured, **~47% of it was schemas of tools that were never invoked.** (Caveat: the capture
goes through a custom `ANTHROPIC_BASE_URL`, which turns off the client's native MCP-schema
deferral, so in an MCP-heavy setup the captured prefix overstates what the native client sends —
in ours, 82,359 tokens captured vs a 41,899-token native median; the ~47% was measured on the
captured prefix.)

```bash
# from the repo root, in the project directory you want audited
python3 -m contextruntime.cli doctor --prefix --cwd /path/to/your/project --sessions 30
```

It runs one `claude -p` against a local capture endpoint (the request is captured and answered with
a non-retryable 400 — **zero tokens billed**), joins it with your recent local session transcripts,
and prints per item: tokens, first-use turn, wasted residency, and an action —
`KEEP / DEFER / DISABLE? / COMPRESS / UNKNOWN` — tagged by who can act on it
(`SUBSCRIPTION_CONFIG` = you, via settings; `GATEWAY_CONTROLLABLE`; `ANTHROPIC_CLIENT_REQUIRED`).
Add `--json` for machine-readable output; `--no-capture` to use transcripts only (currently
crashes — a known bug, see [Current limitations](#current-limitations)).

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
it is what gets you back to native parity before any saving begins. The proxy itself does no
admission (it never touches the `tools` list); this client flag is the whole admission lever.

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
| `CR_GATEWAY_CACHE_ALIGN` | `off` (default) · `cold` · `gated` | `off` = mutate at fixed batch boundaries (the B6 behavior); `cold` = new mutations only when the cache is cold (start / idle gap > TTL); `gated` = cold + break-even rule (as shipped, the break-even branch cannot fire on `anthropic-1h`/`anthropic-5m`, so there `gated` behaves like `cold`). Fired mutations persist (byte-stable) in both aligned modes — but see the forwarding bug under [Current limitations](#current-limitations) |
| `CR_GATEWAY_PROFILE` | `anthropic-1h` (default; the only profile checked against live sessions) · `anthropic-5m` · `openai-auto` · `gemini-implicit` | provider constants for the break-even rule; unknown names fall back to the default (strictest) |
| `CR_GATEWAY_LOG` | path | decision log (JSONL); unset = no log |
| `CR_GATEWAY_PORT` | integer (default 8787) | listen port on 127.0.0.1 |
| `CR_GATEWAY_UPSTREAM` | URL (default `https://api.anthropic.com`) | where requests are relayed |

### Safety properties (what the live runs do and do not show)

- **Fail-open everywhere**: a parse error passes the request through untouched; an upstream 4xx to a
  *mutated* body resends the **original bytes verbatim** (logged as `fallback_original`). Live
  evidence: 0 such events in the 12 B6 enforce sessions whose logs can be audited (all with cache
  alignment off). The 3 B8 v2 sessions applied no mutations, so they test nothing here, and the 2
  B8 v1 sessions survive only as aggregates. The proxy records a fallback only for a 4xx to a
  mutated body; a 5xx, a dropped connection or a client abort leaves no outcome row at all (in one
  B6 session, 40 of 71 requests have no recorded outcome).
- Retirement touches only provably-dead tool results (superseded by a later identical call, or
  untouched ≥ 5 turns) and replaces them with a stub carrying a recovery instruction. Re-reads
  after retirement were never measured as preregistered: the B6 figure (11/36 vs 12/39, held in no
  committed artifact) counts all same-path repeat reads, and B6 retirement lasted one request per
  batch boundary.
- Task quality passed B6's non-inferiority rule exactly at its bound (9 vs 10 of 12; django-16502
  T 0/3 vs N 2/3) and was 9/9 vs 9/9 in B8 v2 on three tasks chosen after B6 that exclude 16502.
  Grading used a local macOS runner, not the official SWE-bench harness (see
  `docs/b6-findings.md`, `docs/b8-findings.md`).

### How to confirm it is working

1. First request of a session: `cache_creation_input_tokens` should be roughly your *lean* prefix
   (ours: ~18k with admission vs ~85k without). If it's huge, admission isn't applied.
2. `fallback_original` lines should be absent (a missing `response_usage` line after a decision
   means the outcome went unrecorded, not that it succeeded).
3. On the default `anthropic-1h` profile, `fire_reason` will be `hold` on every request except a
   cold start or an idle gap longer than the TTL, because the break-even branch cannot fire there
   (see limitations) — a `hold` is not evidence of a correct cost decision.
   **Do not use `persistent_applied` as proof that anything was removed:** because of a known bug
   (`gateway_proxy.py:47`), it is logged even when the stub is not forwarded upstream — the proxy
   re-serializes the body only when the same request also applied a new retirement or stripped
   thinking, so with `CR_GATEWAY_THINKING_KEEP` unset, persisted retirements are counted but not sent.

### Current limitations

- **One proxy process per agent session.** Scheduler state (fired set, thinking frontier, last
  request time) is process-wide; running several concurrent conversations through one proxy mixes
  their gap detection and frontier — and even a later, shorter conversation through the same
  proxy can have thinking stripped up to the old frontier, including its latest assistant
  message. Start one proxy per session (different ports).
- The "1h" cache TTL is soft in practice (we observed no expiry at 65-minute gaps), and both
  `cold` and `gated` fire on an idle gap longer than the TTL (`cachealign.py:79-80`), so either
  may mutate a still-warm cache. On `anthropic-1h`/`anthropic-5m` the break-even branch cannot fire
  as shipped: it needs `0.1·P·8 ≥ (w−0.1)·S`, but the suffix S is counted from the earliest
  pending tool result, so S ≥ P — in practice `gated` = `cold` on these profiles. (The modeled B7
  "gated" savings come from a different replay rule; see `docs/b7-findings.md`.)
- **Known bugs (unfixed):** persistent retirements are not forwarded unless the same request also
  applied a new retirement or stripped thinking (`gateway_proxy.py:47`), while `persistent_applied`
  is still logged; a fired mutation that the API rejects is never rolled back, so it is re-sent
  (and falls back) on every later request that re-serializes the body;
  `doctor --prefix --no-capture` crashes; the doctor's same-project session filter misses project
  paths that contain `_` (e.g. in the username), so its transcript join comes back empty.
- Anthropic-ecosystem-specific levers (thinking-GC, the schema-deferral interaction) have no
  equivalent on other providers; **GPT/OpenAI and local models need an OpenAI-format adapter and a
  calibration pass before any of this applies** — see the porting ladder in `docs/provider-profiles.md`.

## Optional: the Phase-1 observation layer

`python3 -m contextruntime.cli install claude [--project DIR|--global] [--dry-run]` wires an
advisory, fail-open hook journal into Claude Code (uninstall with `uninstall`). It predates the
B-series stack; its transparent-reduction path (`--enable-reduction`) measured ~0.03% live and was
closed — keep it off unless you are reproducing the early experiments.
