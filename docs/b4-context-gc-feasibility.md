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

## Next steps (in order)

1. ~~Pick the mutation site~~ — **done: gateway** (a proxy owning the outbound request).
2. ~~Wire the mutator OBSERVE-first behind a kill-switch~~ — **done: `RetirementGateway`, default off,
   OBSERVE non-mutating + fail-open, validated on real sessions.** Remaining sub-step: deploy the
   gateway in OBSERVE mode on **live** traffic (a real proxy process in front of the API) and collect
   decision logs, to measure the retirable opportunity on production sessions rather than replayed ones.
3. **Turn on ENFORCE behind the kill-switch** once OBSERVE logs look right, starting with the
   provably-safe superseded-only subset before the cold-tail policy.
4. **Graded scale-up** — with a live mutator, run the dozens-of-tasks × reps A/B *with* pass/fail
   grading to convert B3.1's *modeled* 8–11% into a *measured* whole-session number.

The one thing this spike does NOT include is the proxy process itself (the HTTP server that terminates
the client connection and forwards to the API) — that is deployment plumbing, not a feasibility
question; `RetirementGateway.process(body)` is the hook it would call per request.

Frozen B1, the B2 artifacts, and the G1/G2 closure are untouched.
