"""B4 — Context-GC gateway adapter (OBSERVE-first).

A gateway sits between the client and the Anthropic Messages API and owns the outbound request — the
one place B3.3 showed retirement CAN be applied (the subscription client cannot). Each request already
carries the FULL message array, so this adapter is stateless per request: it rebuilds the
`RetirementPlanner` from the messages, computes what is retirable now, and — depending on mode —

  - **off**     : passthrough, no planning (kill-switch; the default);
  - **observe** : plan + LOG what WOULD be freed, return the request byte-for-byte UNCHANGED;
  - **enforce** : additionally apply the retirement to the outbound messages at batch boundaries.

Mirrors how B1 shipped: default off, OBSERVE before ENFORCE, and FAIL-OPEN — any parse error returns
the request untouched. The planner/policy lives in `retirement.py`; this module is only the adapter
that maps Anthropic message shapes to `ObservedObject`s and decides mode.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from .reducers.base import tokens as _tok
from .retirement import (HistoryMutationPlan, InProcessMessageMutator, ObservedObject,
                         RetirementPlanner, DEFAULT_BATCH_TURNS, DEFAULT_LAG)

_READ = {"Read", "NotebookRead"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_KEYED = {"Grep", "Glob", "Bash"}
MODES = ("off", "observe", "enforce")


def gateway_mode_from_env() -> str:
    """CR_GATEWAY_MODE ∈ {off, observe, enforce}; anything else (incl. unset) ⇒ off (safe default)."""
    m = (os.environ.get("CR_GATEWAY_MODE") or "off").strip().lower()
    return m if m in MODES else "off"


def object_key(name: str, inp: dict) -> str:
    """Supersession key. File tools key on PATH (any later touch supersedes the earlier view); keyed
    tools key on their exact invocation. Empty ⇒ not a retirable object."""
    if name in _READ or name in _EDIT:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"path:{os.path.normpath(str(p)).lstrip('./')}" if p else ""
    if name == "Bash":
        return "bash:" + (inp.get("command") or "").strip()
    if name == "Grep":
        return "grep:" + json.dumps({k: inp.get(k) for k in ("pattern", "path", "glob", "type")}, sort_keys=True)
    if name == "Glob":
        return "glob:" + json.dumps({k: inp.get(k) for k in ("pattern", "path")}, sort_keys=True)
    return ""


def _recovery_ref(name: str, inp: dict) -> str:
    if name in _READ or name in _EDIT:
        return f"reread:{inp.get('file_path') or inp.get('notebook_path') or ''}"
    if name == "Bash":
        return "rerun:" + (inp.get("command") or "").strip()[:80]
    return f"rerun:{name.lower()}"


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def message_objects(messages):
    """(objects, n_turns) from an Anthropic messages array. A tool_result inherits the turn of the
    assistant message that requested it (turn = running count of assistant messages)."""
    uses, objs, turn = {}, [], 0
    for msg in messages:
        blocks = msg.get("content")
        blocks = blocks if isinstance(blocks, list) else []
        if msg.get("role") == "assistant":
            turn += 1
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses[b.get("id")] = (b.get("name", ""), b.get("input") or {}, turn)
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                ref = uses.get(b.get("tool_use_id"))
                if ref:
                    name, inp, tt = ref
                    key = object_key(name, inp)
                    if key:
                        objs.append(ObservedObject(b["tool_use_id"], tt, key,
                                                   _tok(_result_text(b.get("content"))), _recovery_ref(name, inp)))
    return objs, turn


@dataclass
class GatewayDecision:
    turn: int
    mode: str
    n_objects: int
    n_retirable: int
    tokens_retirable: int
    is_batch_boundary: bool
    applied: int = 0                       # tool_results actually stubbed (enforce only)


class RetirementGateway:
    """Stateless-per-request gateway adapter. `process(body)` returns (body_out, decision).
    OBSERVE never mutates; ENFORCE mutates only at batch boundaries; FAIL-OPEN on any error."""

    def __init__(self, *, mode: str = None, lag: int = DEFAULT_LAG, batch_turns: int = DEFAULT_BATCH_TURNS,
                 log_path: str = None) -> None:
        self.mode = mode if mode in MODES else gateway_mode_from_env()
        self.lag = lag
        self.batch_turns = batch_turns
        self.log_path = log_path

    def _log(self, record: dict) -> None:
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:      # noqa: BLE001 — logging must never break the request path
            pass

    def _plan(self, messages):
        objs, n_turns = message_objects(messages)
        planner = RetirementPlanner(lag=self.lag, batch_turns=self.batch_turns)
        for o in sorted(objs, key=lambda o: o.turn):
            planner.observe(o)
        return objs, n_turns, planner.plan(n_turns, force=True)   # full retirable set at the latest turn

    def process(self, body: dict):
        if self.mode == "off":
            return body, None
        try:
            messages = body.get("messages") or []
            objs, n_turns, plan = self._plan(messages)
            boundary = self.batch_turns > 0 and n_turns > 0 and n_turns % self.batch_turns == 0
            dec = GatewayDecision(turn=n_turns, mode=self.mode, n_objects=len(objs),
                                  n_retirable=len(plan.retirements), tokens_retirable=plan.tokens_freed,
                                  is_batch_boundary=boundary)
            if self.mode == "enforce" and boundary and plan.retirements:
                dec.applied = InProcessMessageMutator().apply(plan, messages).applied
            self._log(asdict(dec))
            return body, dec
        except Exception as e:      # noqa: BLE001 — FAIL-OPEN: never break a request
            self._log({"error": str(e)[:200], "mode": self.mode})
            return body, None


def summarize_log(log_path: str) -> dict:
    """Aggregate an OBSERVE-mode decision log into the measurable opportunity: per-request retirable
    tokens, and the batch-boundary token-turn saving that ENFORCE would have realized."""
    rows = []
    with open(log_path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:      # noqa: BLE001
                continue
            if "turn" in r:
                rows.append(r)
    if not rows:
        return {"requests": 0}
    boundaries = [r for r in rows if r.get("is_batch_boundary")]
    peak_turn = max(r["turn"] for r in rows)
    freed_at_boundaries = sum(r["tokens_retirable"] for r in boundaries)
    return {"requests": len(rows), "peak_turn": peak_turn,
            "mean_retirable_tokens": round(sum(r["tokens_retirable"] for r in rows) / len(rows), 1),
            "max_retirable_tokens": max(r["tokens_retirable"] for r in rows),
            "batch_boundaries": len(boundaries),
            "tokens_retirable_at_last_boundary": (boundaries[-1]["tokens_retirable"] if boundaries else 0),
            "applied_total": sum(r.get("applied", 0) for r in rows)}
