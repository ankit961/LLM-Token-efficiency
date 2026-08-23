"""B4 — Production Context GC: the retirement PLANNER, separated from the history MUTATOR.

The B3 research line established the policy (retire a tool output once it is superseded or has gone
cold, keep an exact recovery handle, batch the rewrites) and its ceiling (~8-11% modeled safe
single-window residency saving, ~95-99% mechanically safe, live-sanity-checked in B3.3). This module
turns that policy into a shippable abstraction with the seam that the research made unavoidable:

    RetirementPlanner  →  HistoryMutationPlan  →  HistoryMutator

The PLANNER is pure, forward-only (streaming: it sees objects turn by turn and never looks ahead) and
fully testable offline. The MUTATOR is the environment-dependent half — and B3.3 established that the
Claude Code subscription client exposes no runtime API to rewrite prior context, so its mutator is
`Unsupported`; a gateway or a custom agent loop that owns the message array can apply a plan in process.

Retirement policy ≠ history-mutation mechanism. That separation is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

DEFAULT_LAG = 5                      # turns a key may go untouched before its tail is 'cold' (B3.1 knee)
DEFAULT_BATCH_TURNS = 10            # amortize the cache-rewrite cost over ~this many turns (B3.0/B3.2)


@dataclass(frozen=True)
class ObservedObject:
    """A tool output that entered the prefix. `key` is the supersession key (e.g. 'path:a/b.py',
    'bash:pytest -q'); `recovery_ref` is how the retired payload comes back (a `result://<hash>`
    handle, or a re-run/re-read instruction). Content itself is NOT held here — only what the planner
    needs to reason about residency."""
    obj_id: str
    turn: int
    key: str
    tokens: int
    recovery_ref: str = ""


@dataclass(frozen=True)
class Retirement:
    obj_id: str
    reason: str                    # 'superseded' (provably dead) | 'cold_tail' (untouched >= lag)
    retire_turn: int
    tokens_freed: int
    recovery_ref: str
    replacement: str               # stub text that names the recovery_ref


@dataclass(frozen=True)
class HistoryMutationPlan:
    at_turn: int
    retirements: Tuple[Retirement, ...]
    tokens_freed: int

    def is_empty(self) -> bool:
        return not self.retirements


def _stub(recovery_ref: str) -> str:
    tail = f" Recover via {recovery_ref}." if recovery_ref else " Re-run the tool or re-read the file if needed."
    return "[Context note: this earlier tool output was retired to save context." + tail + "]"


class RetirementPlanner:
    """Forward-only planner. Feed it every tool output via `observe`; call `plan(turn)` at batch
    boundaries to get the retirements to apply. Provably-dead SUPERSEDED objects and COLD-TAIL objects
    (whose key has not been touched for `lag` turns) are retired; the single most-recent object of a
    still-warm key is always kept. Idempotent: an object is planned at most once."""

    def __init__(self, *, lag: int = DEFAULT_LAG, batch_turns: int = DEFAULT_BATCH_TURNS,
                 min_batch_tokens: int = 0) -> None:
        self.lag = lag
        self.batch_turns = batch_turns
        self.min_batch_tokens = min_batch_tokens
        self._objs: List[ObservedObject] = []
        self._last_touch: Dict[str, int] = {}
        self._superseded: set = set()          # obj_ids provably replaced by a later same-key object
        self._planned: set = set()             # obj_ids already emitted in a plan
        self._last_plan_turn = 0

    # -- ingest -------------------------------------------------------------
    def observe(self, obj: ObservedObject) -> None:
        prev = self._last_touch.get(obj.key)
        if prev is not None:                   # a later object with this key supersedes all earlier ones
            for o in self._objs:
                if o.key == obj.key and o.turn <= prev and o.obj_id not in self._superseded:
                    self._superseded.add(o.obj_id)
        self._objs.append(obj)
        self._last_touch[obj.key] = max(prev or 0, obj.turn)

    def touch(self, key: str, turn: int) -> None:
        """Mark a key warm without a new object (e.g. the agent edited/referenced that path)."""
        self._last_touch[key] = max(self._last_touch.get(key, 0), turn)

    # -- policy -------------------------------------------------------------
    def _retirable(self, now: int) -> List[ObservedObject]:
        out = []
        for o in self._objs:
            if o.obj_id in self._planned or o.turn > now:
                continue
            cold = self._last_touch.get(o.key, o.turn) + self.lag <= now
            if o.obj_id in self._superseded or cold:
                out.append(o)
        return out

    def plan(self, now: int, *, force: bool = False) -> HistoryMutationPlan:
        """Return the retirements to apply at `now`. Emits an empty plan unless a batch boundary is due
        (>= batch_turns since the last plan, or accumulated freed tokens >= min_batch_tokens), so the
        caller pays the cache-rewrite cost rarely. `force=True` flushes regardless."""
        ready = self._retirable(now)
        tokens = sum(o.tokens for o in ready)
        due = force or (now - self._last_plan_turn >= self.batch_turns) or (
            self.min_batch_tokens and tokens >= self.min_batch_tokens)
        if not ready or not due:
            return HistoryMutationPlan(now, (), 0)
        rets = tuple(Retirement(o.obj_id,
                                "superseded" if o.obj_id in self._superseded else "cold_tail",
                                now, o.tokens, o.recovery_ref, _stub(o.recovery_ref))
                     for o in ready)
        for o in ready:
            self._planned.add(o.obj_id)
        self._last_plan_turn = now
        return HistoryMutationPlan(now, rets, tokens)


# ---------------------------------------------------------------------------
# Mechanism: apply a plan to real context history. Policy above is agnostic to this.
# ---------------------------------------------------------------------------
@dataclass
class MutationResult:
    applied: int
    tokens_freed: int
    note: str = ""


class HistoryMutator(Protocol):
    @property
    def supported(self) -> bool: ...
    def apply(self, plan: HistoryMutationPlan, history: object) -> MutationResult: ...


class UnsupportedMutator:
    """Claude Code subscription client. B3.3 established there is NO runtime API to rewrite prior
    context — the experiment had to hand-edit a stored resume transcript, which is not a production
    mechanism. So retirement is planned but not applied here."""
    backend = "claude-code-subscription"
    supported = False

    def apply(self, plan: HistoryMutationPlan, history: object = None) -> MutationResult:
        return MutationResult(0, 0, "unsupported: no runtime context-rewrite API on this client")


class InProcessMessageMutator:
    """Gateway / custom agent loop that OWNS the message array. Applies a plan in process by replacing
    the retired tool_result content with its stub. `history` is a list of message dicts (Anthropic
    Messages shape); the recovery payload lives in the caller's CAS behind recovery_ref."""
    backend = "in-process-message-array"
    supported = True

    def apply(self, plan: HistoryMutationPlan, history: List[dict]) -> MutationResult:
        by_id = {r.obj_id: r for r in plan.retirements}
        applied = freed = 0
        for msg in history:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    r = by_id.get(block.get("tool_use_id"))
                    if r is not None and block.get("content") != r.replacement:
                        block["content"] = r.replacement
                        applied += 1
                        freed += r.tokens_freed
        return MutationResult(applied, freed, f"backend={self.backend}")


def simulate(objects: List[ObservedObject], total_turns: int, *, lag: int = DEFAULT_LAG,
             batch_turns: int = DEFAULT_BATCH_TURNS) -> dict:
    """Run the forward planner over a session's objects (offline validation / what-if). Returns the
    plans emitted and the total token-turns freed, so the product policy can be checked against the B3
    research numbers."""
    planner = RetirementPlanner(lag=lag, batch_turns=batch_turns)
    by_turn: Dict[int, List[ObservedObject]] = {}
    for o in objects:
        by_turn.setdefault(o.turn, []).append(o)
    plans = []
    tokturns_freed = 0
    for now in range(1, total_turns + 1):
        for o in by_turn.get(now, []):
            planner.observe(o)
        p = planner.plan(now, force=(now == total_turns))
        if not p.is_empty():
            plans.append(p)
            tokturns_freed += sum(r.tokens_freed * max(total_turns - now, 0) for r in p.retirements)
    return {"n_plans": len(plans), "n_retired": sum(len(p.retirements) for p in plans),
            "tokturns_freed": tokturns_freed, "plans": plans}
