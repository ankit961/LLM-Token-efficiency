# B4 — Production Context GC: feasibility spike

**Status: first spike merged.** This is a *production feasibility* step, not another oracle experiment.
It turns the frozen B3 policy (`B3_DECISION.md`) into a shippable abstraction and pins down the one
question the research made binding: **where can context history actually be mutated?**

Module: `contextruntime/retirement.py`. Tests: `tests/test_retirement.py`.

## The one separation that matters

    RetirementPlanner  →  HistoryMutationPlan  →  HistoryMutator
      (policy)              (data)                  (mechanism)

**Retirement policy ≠ history-mutation mechanism.** Conflating them is what made the earlier work feel
blocked ("Claude Code can't rewrite context, therefore retirement is impossible"). Split apart, the
policy is buildable and fully testable *today*, and the mechanism becomes a per-environment adapter
question — answerable independently, and already answered for the subscription client.

## The planner (built, tested)

`RetirementPlanner` is **forward-only**: it sees each tool output via `observe(ObservedObject)` and
never looks ahead (a real runtime has no future). It carries the frozen B3 policy:

- **Superseded** — a later object with the same key (`path:a/b.py`, `bash:pytest -q`, …) makes the
  earlier one provably dead. Retirable immediately.
- **Cold tail** — a key untouched for `lag` turns (default **5**, the B3.1 knee) goes cold; its objects
  become retirable. The single most-recent object of a still-**warm** key is always kept.
- **Batched** — `plan(turn)` emits retirements only at batch boundaries (default every **10** turns, or
  a token threshold), so the caller pays the cache-rewrite cost rarely (B3.0/B3.2: batching keeps ~90%
  of the benefit at a fraction of the rewrites). `force=True` flushes.
- **Recoverable** — every `Retirement` carries the object's `recovery_ref` (a `result://<hash>` handle
  from B1's `livecas`, or a re-run/re-read instruction) and a stub that names it. Nothing is dropped
  irrecoverably — the same invariant B1 shipped.

`simulate(objects, total_turns)` runs the planner forward over a session so the product policy can be
checked against the B3 research numbers offline.

## The mechanism (the binding constraint)

`HistoryMutator` is the adapter. Three backends, with the honest status the research established:

| backend | status | why |
|---|---|---|
| **Claude Code subscription** (`UnsupportedMutator`) | ❌ unsupported | B3.3 confirmed there is **no runtime API to rewrite prior context**. Every `claude -p` mode (including `--input-format stream-json` and `--resume`) has the client own its context and execute tools itself; the experiment had to hand-edit a *stored resume transcript*, which is an experiment mechanism, not a production architecture. |
| **Gateway** (proxy that sits between client and the API) | ✅ supported | It owns the outbound message array and can apply a plan in process before each request — `InProcessMessageMutator`. This is the most likely production home if Claude Code stays the client. |
| **Custom agent loop** (SDK, owns the message list) | ✅ supported | Same in-process mechanism; trivially applies a plan. |

`InProcessMessageMutator.apply(plan, history)` replaces the retired `tool_result` content with its stub
in a list of Anthropic-shaped messages, returning what it freed. That is the whole mechanism where it
*is* supported — the difficulty was never the edit, it was *having a place to make it*.

## Gateway adapter — built and OBSERVE-validated (`contextruntime/gateway.py`)

The gateway backend is now wired. `RetirementGateway.process(body)` is stateless per request (each API
call already carries the full message array): it maps Anthropic message shapes to `ObservedObject`s,
rebuilds the planner, and dispatches on `CR_GATEWAY_MODE`:

- **off** (default kill-switch) — passthrough, no planning.
- **observe** — plan + log what WOULD be freed, return the request **byte-for-byte unchanged**.
- **enforce** — additionally stub the retired `tool_result`s at batch boundaries.

It is **fail-open** (any parse error returns the request untouched) and OBSERVE is **non-mutating by
construction** (the mutator is only invoked under `enforce`). Run over 5 real Step-7 sessions in OBSERVE
mode (`corpus/analysis/b4-gateway-observe.json`), the non-mutation invariant held on every request, and
it surfaced ~1.8–4.0k retirable tokens per request (up to ~8.2k), ~3–4 batch boundaries per ~40-turn
session — the expected shape, and consistent with the B3 residency numbers. `summarize_log()` turns an
OBSERVE log into that measurable opportunity.

This is the B1 shipping pattern exactly: default off, OBSERVE before ENFORCE, fail-open, decision log.

## What this spike establishes

- The **policy half is done and safe-by-construction**: deterministic, forward-only, recoverable,
  batched, unit-tested. No further research is needed to ship the planner.
- The **mechanism half is a placement decision, not a percentage-point experiment.** On the
  subscription client it is unsupported; in a gateway or custom loop it is a few lines. So if Claude
  Code subscription is the primary target, **the gateway is the real B4 deliverable**, and that
  limitation now matters more than any further offline measurement.

## What is explicitly out of scope here (and why)

- **No live token-savings number.** That needs a supported mutator wired into a real session with
  pass/fail grading — a graded scale-up, deferred until a mutator exists to measure.
- **No new policy tuning.** `lag≈5–10` and batch≈10 are the frozen B3 operating points; re-tuning is a
  later optimization, not a feasibility question.

## Thinking-GC — built, OBSERVE-counted, ENFORCE-validated live (`gateway.py`, 2026-08-23)

`docs/path-to-50.md` found that **retained thinking is ~11.3% of resident token-turns** on the django
sessions and is *invisible* in transcripts: on display-omitted models the client holds only the
`signature`, but the server decrypts it back into context and — on keep-all models (Opus 4.5+, Sonnet
4.6+, Fable/Mythos 5) — *"previous thinking blocks remain in context, count toward the window, and are
billed as input."* The API rules for the client are explicit: *"outside tool use, omit prior turns'
thinking"* is **allowed**; *"within the latest assistant message"* the thinking sequence must stay
intact (including `redacted_thinking`), or the request is rejected with a 400.

So thinking-GC is B3's policy applied to thinking, with one important economic difference:

- **Policy**: `CR_GATEWAY_THINKING_KEEP=N` keeps thinking only in the last N assistant messages
  (N ≥ 1; the latest is never touched); everything older is dropped (`thinking_gc`). A message is never
  emptied. OBSERVE counts what would be stripped (`thinking_opportunity`); ENFORCE strips on every call.
- **Cache-cheap by construction**: the edit point is always the message that just left the keep window
  — at the *tail* of the prefix — so the invalidated suffix is small and constant. Unlike B3 retirement
  (deep in the prefix, hence batched), thinking-GC can run every call.
- **Fail-open at the response level**: if upstream answers 4xx to a *mutated* body, the proxy resends
  the **original** bytes and logs `fallback_original`. This protects both thinking-GC and B3 enforce
  against any rule we have wrong.
- **Real usage now logged**: the proxy forwards with identity encoding and extracts `usage` from the
  SSE `message_start`/`message_delta` events (or JSON) — so the gateway measures its own effect.

**Live validation** (`corpus/analysis/b4-thinking-gc-live.json`, ~$0.80): the same tiny coding task
through the proxy with `CR_GATEWAY_MODE=enforce CR_GATEWAY_THINKING_KEEP=1` on Sonnet 5 (a keep-all
model): **7 API calls, all 200, 0 fallbacks** — the API accepted every request with prior-turn thinking
stripped mid tool-use loop — and the task completed correctly (tests pass on disk). The stripped turns
re-created only **122 / 834 / 142** cache tokens, confirming the tail-edit cheapness. Magnitude is
workload-dependent: this trivial headless task produced ~400-byte signatures (~100 thinking tokens per
block), so the saving here is negligible; the 11.3% share was measured on reasoning-heavy sessions, and
interactive Opus sessions are heavier still. The mechanism, its legality, and its cache economics are
what this validates — not a percentage.

## Next steps (in order)

1. ~~Pick the mutation site~~ — **done: gateway** (a proxy owning the outbound request).
2. ~~Wire the mutator OBSERVE-first behind a kill-switch~~ — **done: `RetirementGateway`, default off,
   OBSERVE non-mutating + fail-open, validated on real sessions.**
3. ~~Make OBSERVE runnable as a real proxy~~ — **done: `contextruntime/gateway_proxy.py`** (stdlib-only
   HTTP proxy; relays the client's own auth, streams responses through by connection-close). Run it and
   point the client at it:

       CR_GATEWAY_MODE=observe CR_GATEWAY_LOG=gw.jsonl python -m contextruntime.gateway_proxy
       export ANTHROPIC_BASE_URL=http://127.0.0.1:8787

   In OBSERVE mode it forwards request bytes **unchanged** (integration-tested against a fake upstream)
   and appends a decision per request; `summarize_log(gw.jsonl)` reports the opportunity. Remaining
   sub-step: actually run production traffic through it and read the logs.
4. **Turn on ENFORCE behind the kill-switch** (`CR_GATEWAY_MODE=enforce`) once OBSERVE logs look right,
   starting with the provably-safe superseded-only subset before the cold-tail policy.
5. **Graded scale-up** — with a live mutator, run the dozens-of-tasks × reps A/B *with* pass/fail
   grading to convert B3.1's *modeled* 8–11% into a *measured* whole-session number.

What remains is **operational**, not a feasibility question: deciding where the proxy runs, and running
real sessions through it. The proxy buffers nothing but does terminate TLS at the client's chosen
`ANTHROPIC_BASE_URL`, so production use needs the usual proxy hardening (TLS, timeouts, retries) — out
of scope for the spike, which proves the mechanism end-to-end.

Frozen B1, the B2 artifacts, and the G1/G2 closure are untouched.
