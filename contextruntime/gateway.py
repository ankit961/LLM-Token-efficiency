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
import time
from dataclasses import asdict, dataclass

from .reducers.base import tokens as _tok
from .retirement import (HistoryMutationPlan, InProcessMessageMutator, ObservedObject,
                         RetirementPlanner, DEFAULT_BATCH_TURNS, DEFAULT_LAG)

_READ = {"Read", "NotebookRead"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_KEYED = {"Grep", "Glob", "Bash"}
MODES = ("off", "observe", "enforce")
_THINKING = ("thinking", "redacted_thinking")


def thinking_keep_from_env():
    """CR_GATEWAY_THINKING_KEEP = N: keep thinking blocks only in the last N assistant messages
    (N >= 1 — the latest assistant message is never touched). Unset/invalid ⇒ thinking-GC off."""
    v = os.environ.get("CR_GATEWAY_THINKING_KEEP")
    try:
        n = int(v) if v else 0
    except ValueError:
        return None
    return n if n >= 1 else None


def thinking_opportunity(messages, keep_last):
    """Count the thinking/redacted_thinking blocks (and their signature bytes — the client holds only
    signatures on display=omitted models) that thinking-GC WOULD strip: all assistant messages except
    the last `keep_last`. Pure; never mutates."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    n_blocks = sig_bytes = 0
    for i in idx[:-keep_last] if keep_last > 0 else []:
        for b in (messages[i].get("content") if isinstance(messages[i].get("content"), list) else []):
            if isinstance(b, dict) and b.get("type") in _THINKING:
                n_blocks += 1
                sig_bytes += len(b.get("signature") or b.get("data") or "")
    return n_blocks, sig_bytes


def thinking_gc(messages, keep_last):
    """Strip thinking/redacted_thinking blocks from every assistant message EXCEPT the last `keep_last`
    (docs: 'outside tool use, omit prior turns' thinking'; the latest assistant message's thinking
    sequence must stay intact). A message is never emptied — if stripping would leave no blocks it is
    left alone. Returns (n_blocks_stripped, signature_bytes_stripped). Mutates in place."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    n_blocks = sig_bytes = 0
    for i in idx[:-keep_last] if keep_last > 0 else []:
        c = messages[i].get("content")
        if not isinstance(c, list):
            continue
        kept = [b for b in c if not (isinstance(b, dict) and b.get("type") in _THINKING)]
        if not kept or len(kept) == len(c):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") in _THINKING:
                n_blocks += 1
                sig_bytes += len(b.get("signature") or b.get("data") or "")
        messages[i]["content"] = kept
    return n_blocks, sig_bytes


def thinking_gc_upto(messages, frontier_turn):
    """B7 persistent variant of thinking-GC: strip thinking blocks from assistant messages whose
    1-based assistant index is <= frontier_turn. The frontier only advances at cache-aligned fire
    moments, so between fires every request strips the SAME set — byte-stable, no new cache
    invalidation. Returns (n_blocks_stripped, signature_bytes_stripped). Mutates in place."""
    idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    n_blocks = sig_bytes = 0
    for a_turn, i in enumerate(idx, start=1):
        if a_turn > frontier_turn:
            break
        c = messages[i].get("content")
        if not isinstance(c, list):
            continue
        kept = [b for b in c if not (isinstance(b, dict) and b.get("type") in _THINKING)]
        if not kept or len(kept) == len(c):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") in _THINKING:
                n_blocks += 1
                sig_bytes += len(b.get("signature") or b.get("data") or "")
        messages[i]["content"] = kept
    return n_blocks, sig_bytes


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
    thinking_strippable: int = 0           # thinking blocks outside the last N assistant messages
    thinking_sig_bytes: int = 0            # their signature bytes (proxy for retained thinking size)
    thinking_stripped: int = 0             # blocks actually removed (enforce + CR_GATEWAY_THINKING_KEEP)
    ts: float = 0.0                        # request wall-clock (B7: enables gap analysis from logs)
    align: str = "off"                     # CR_GATEWAY_CACHE_ALIGN mode in effect
    fired: bool = False                    # cache-aligned: NEW mutations introduced this request
    fire_reason: str = ""                  # cold-start | ttl-gap | break-even | hold | align-off
    gap_s: float = -1.0                    # seconds since previous request (-1 on the first)
    pending_tokens: int = 0                # retirable tokens held back by the scheduler
    suffix_tokens_est: int = 0             # estimated tokens after the earliest pending edit point
    persistent_applied: int = 0            # previously fired retirements re-applied (byte-stable)


class RetirementGateway:
    """Stateless-per-request gateway adapter. `process(body)` returns (body_out, decision).
    OBSERVE never mutates; ENFORCE mutates only at batch boundaries; FAIL-OPEN on any error."""

    def __init__(self, *, mode: str = None, lag: int = DEFAULT_LAG, batch_turns: int = DEFAULT_BATCH_TURNS,
                 log_path: str = None, thinking_keep: int = None, align: str = None) -> None:
        from .cachealign import ALIGN_MODES, CacheAlignedScheduler, align_mode_from_env
        from .providers import profile_from_env
        self.mode = mode if mode in MODES else gateway_mode_from_env()
        self.lag = lag
        self.batch_turns = batch_turns
        self.log_path = log_path
        self.thinking_keep = thinking_keep if thinking_keep is not None else thinking_keep_from_env()
        self.align = align if align in ALIGN_MODES else align_mode_from_env()
        self.profile = profile_from_env()          # CR_GATEWAY_PROFILE; default = validated anthropic-1h
        self.scheduler = CacheAlignedScheduler.from_profile(self.align, self.profile)

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

    @staticmethod
    def _suffix_tokens_est(messages, after_turn: int) -> int:
        """Rough token count of everything AFTER assistant turn `after_turn` — the suffix a fire at
        that depth would re-create (the retired tool_result sits in the next user message, so the
        count starts there). Serialization chars / 4; gates WHEN to fire, never correctness."""
        a_turn = 0
        chars = 0
        counting = False
        for m in messages:
            if counting:
                chars += len(json.dumps(m.get("content"), default=str))
            if m.get("role") == "assistant":
                a_turn += 1
                if a_turn >= after_turn:
                    counting = True
        return chars // 4

    def process(self, body: dict):
        if self.mode == "off":
            return body, None
        try:
            messages = body.get("messages") or []
            objs, n_turns, plan = self._plan(messages)
            boundary = self.batch_turns > 0 and n_turns > 0 and n_turns % self.batch_turns == 0
            dec = GatewayDecision(turn=n_turns, mode=self.mode, n_objects=len(objs),
                                  n_retirable=len(plan.retirements), tokens_retirable=plan.tokens_freed,
                                  is_batch_boundary=boundary, ts=time.time(), align=self.align)
            if self.align != "off" and self.mode == "enforce":
                # B7 cache-aligned path: fired mutations re-apply every request (byte-stable);
                # NEW mutations only at cold-start / TTL-gap / break-even moments.
                fired_ids = self.scheduler.fired_keys
                persistent = [r for r in plan.retirements if r.obj_id in fired_ids]
                pending = [r for r in plan.retirements if r.obj_id not in fired_ids]
                if persistent:
                    sub = HistoryMutationPlan(plan.at_turn, tuple(persistent),
                                              sum(r.tokens_freed for r in persistent))
                    dec.persistent_applied = InProcessMessageMutator().apply(sub, messages).applied
                turn_of = {o.obj_id: o.turn for o in objs}
                earliest = min((turn_of.get(r.obj_id, n_turns) for r in pending), default=n_turns)
                suffix_est = self._suffix_tokens_est(messages, earliest) if pending else 0
                fd = self.scheduler.decide(
                    [(r.obj_id, turn_of.get(r.obj_id, n_turns), r.tokens_freed) for r in pending],
                    suffix_est, now_ts=dec.ts)
                dec.fire_reason, dec.gap_s = fd.reason, (fd.gap_s if fd.gap_s is not None else -1.0)
                dec.pending_tokens, dec.suffix_tokens_est = fd.pending_tokens, suffix_est
                if fd.fire:
                    dec.fired = True
                    if pending:
                        sub = HistoryMutationPlan(plan.at_turn, tuple(pending),
                                                  sum(r.tokens_freed for r in pending))
                        dec.applied = InProcessMessageMutator().apply(sub, messages).applied
                    self.scheduler.commit([r.obj_id for r in pending], n_turns)
                if self.thinking_keep:
                    dec.thinking_strippable, dec.thinking_sig_bytes = thinking_opportunity(messages, self.thinking_keep)
                    dec.thinking_stripped, _ = thinking_gc_upto(messages, self.scheduler.strip_frontier)
            else:
                if self.mode == "enforce" and boundary and plan.retirements:
                    dec.applied = InProcessMessageMutator().apply(plan, messages).applied
                if self.thinking_keep:
                    dec.thinking_strippable, dec.thinking_sig_bytes = thinking_opportunity(messages, self.thinking_keep)
                    if self.mode == "enforce" and dec.thinking_strippable:
                        # cache-cheap every call: the edit point is always near the tail (the message that
                        # just left the keep window), so the invalidated suffix is small and constant
                        dec.thinking_stripped, _ = thinking_gc(messages, self.thinking_keep)
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
