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

1. **Pick the mutation site** — gateway vs custom loop — for the primary target. This is the decision
   that unblocks everything downstream.
2. **Wire `InProcessMessageMutator` into that site** behind a kill-switch, OBSERVE-mode first (plan and
   log, do not mutate), mirroring how B1 shipped.
3. **Graded scale-up** — once a mutator is live, run the dozens-of-tasks × reps A/B *with* pass/fail
   grading to convert B3.1's *modeled* 8–11% into a *measured* whole-session number.

Frozen B1, the B2 artifacts, and the G1/G2 closure are untouched.
