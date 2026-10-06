"""B7 — cache-aligned mutation scheduling for the retirement gateway.

B6 measured the problem: every history mutation invalidates the prompt cache from the edit point,
and with this client's 1-hour-TTL cache (writes at 2.0× base input price) the re-created suffix
eats the read savings on short cache-hot sessions. The B7 replay (calibrated to 0.0%/≈7% error on
the 24 live B6 sessions) shows the two regimes:

  short headless sessions:  mutation is dollar-NEGATIVE mid-session; the only free moments are
                            cold starts and TTL-expired idle gaps (there are none back-to-back)
  long interactive sessions: read savings dominate; mutations pay for themselves, and idle gaps
                            (>TTL) provide zero-cost fire windows

The scheduler below adapts across both regimes with two rules and one invariant:

  FIRE when the cache is already cold — first request, or the idle gap since the previous request
  exceeded the TTL (the suffix re-creates either way; mutating then is free), or (mode "gated")
  when the removed tokens pay back the rewrite:

      read_mult · R · (E + 1)  ≥  (write_mult − read_mult) · max(S − R, 0)

  R = tokens the mutation actually removes (pending outputs minus their stubs); S = the suffix
  from the edit point to the end of the request. Firing re-creates S − R at the write price
  instead of reading it, and saves reading R on this call and on each of the E expected later
  calls. (Fixed 2026-10-06: the shipped rule compared R·E against the whole S. S contains R, so
  on Anthropic prices that inequality could never hold and break-even never fired.) Counting
  the request's newest content inside S over-states the cost, so the rule errs toward holding.

  PERSISTENT once fired: a fired mutation re-applies to EVERY subsequent request, so the byte
  stream stays prefix-stable between fires and only NEW mutations ever invalidate. (The B6
  gateway applied retirements only on batch-boundary requests and let history snap back in
  between — each boundary paid the invalidation again and the residency saving lasted one
  request.)

State is per conversation (`SchedulerRegistry`): one proxy usually carries several conversations
(side calls, subagents, parallel sessions), and they must not share a fired set, a thinking frontier
or a request clock. The scheduler decides WHEN, never WHAT — safety stays entirely in
RetirementPlanner. A conversation whose mutated request the API rejects is `tripped`: the gateway
stops mutating it for the rest of the process ("does nothing when uncertain").
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set, Tuple

ALIGN_MODES = ("off", "cold", "gated")
DEFAULT_TTL_S = 3600.0            # the captured client requests cache_control ttl "1h"
DEFAULT_WRITE_MULT = 2.0          # 1h-tier cache-write price, × base input
DEFAULT_READ_MULT = 0.1
DEFAULT_E_REMAINING = 8           # conservative expected remaining calls (preregistered constant)


def align_mode_from_env() -> str:
    m = (os.environ.get("CR_GATEWAY_CACHE_ALIGN") or "off").strip().lower()
    return m if m in ALIGN_MODES else "off"


@dataclass
class FireDecision:
    fire: bool
    reason: str                   # "cold-start" | "ttl-gap" | "break-even" | "hold"
    gap_s: Optional[float] = None
    pending_tokens: int = 0
    suffix_tokens_est: int = 0
    removable_tokens: int = 0


@dataclass
class CacheAlignedScheduler:
    mode: str = "off"
    ttl_s: float = DEFAULT_TTL_S
    write_mult: float = DEFAULT_WRITE_MULT
    read_mult: float = DEFAULT_READ_MULT
    e_remaining: int = DEFAULT_E_REMAINING
    last_request_ts: Optional[float] = None
    fired_keys: Set[str] = field(default_factory=set)
    strip_frontier: int = 0       # thinking stripped for assistant turns <= this (persistent)
    tripped: bool = False         # the API rejected a mutated request: stop mutating this conversation

    def decide(self, pending: List[Tuple[str, int, int]], suffix_tokens_est: int,
               *, removable_tokens: Optional[int] = None,
               now_ts: Optional[float] = None) -> FireDecision:
        """pending: (object_key, turn, tokens_est) for retirable objects NOT yet fired.
        suffix_tokens_est: tokens from the edit point to the end of the request (it contains the
        pending outputs). removable_tokens: what firing actually removes (pending minus stubs);
        defaults to the pending total."""
        now = time.time() if now_ts is None else now_ts
        gap = None if self.last_request_ts is None else now - self.last_request_ts
        self.last_request_ts = now
        pending_tokens = sum(t for _, _, t in pending)
        removable = pending_tokens if removable_tokens is None else max(int(removable_tokens), 0)

        def fd(fire, reason):
            return FireDecision(fire, reason, gap, pending_tokens, suffix_tokens_est, removable)

        if self.mode == "off":
            return fd(True, "align-off")
        if gap is None:
            return fd(True, "cold-start")
        if gap > self.ttl_s:
            return fd(True, "ttl-gap")
        if self.mode == "gated" and removable > 0 and self.breaks_even(removable, suffix_tokens_est):
            return fd(True, "break-even")
        return fd(False, "hold")

    def breaks_even(self, removable_tokens: int, suffix_tokens_est: int) -> bool:
        """read · R · (E + 1) ≥ (write − read) · max(S − R, 0) — see the module docstring."""
        gain = self.read_mult * removable_tokens * (self.e_remaining + 1)
        cost = (self.write_mult - self.read_mult) * max(suffix_tokens_est - removable_tokens, 0)
        return gain >= cost

    def commit(self, keys, n_turns: int, *, keep: int = 1) -> None:
        """Record a fire: these mutations now re-apply to every subsequent request, and thinking
        is stripped from every assistant turn except the last `keep`."""
        self.fired_keys.update(keys)
        self.strip_frontier = max(self.strip_frontier, n_turns - max(keep, 1))

    @classmethod
    def from_profile(cls, mode: str, profile) -> "CacheAlignedScheduler":
        """Build the scheduler from a `contextruntime.providers.ProviderProfile` — the ONLY
        provider-specific inputs the break-even rule needs. anthropic-1h ⇒ break-even 19 reads
        (hold on short sessions); free-write providers ⇒ break-even 1 (fire almost always)."""
        return cls(mode=mode, ttl_s=profile.ttl_s, write_mult=profile.write_mult,
                   read_mult=profile.read_mult)


class SchedulerRegistry:
    """Per-conversation scheduler state: process-lived, keyed by `gateway.conversation_key`,
    LRU-bounded so a long-running proxy cannot grow without limit. `mode` records the align mode
    the states were built for; the proxy starts a fresh registry when the mode changes."""

    def __init__(self, mode: str, factory: Callable[[], CacheAlignedScheduler],
                 max_conversations: int = 128) -> None:
        self.mode = mode
        self._factory = factory
        self._max = max(int(max_conversations), 1)
        self._by_key: "OrderedDict[str, CacheAlignedScheduler]" = OrderedDict()

    def get(self, key: str) -> CacheAlignedScheduler:
        s = self._by_key.get(key)
        if s is None:
            s = self._factory()
            self._by_key[key] = s
            while len(self._by_key) > self._max:
                self._by_key.popitem(last=False)
        else:
            self._by_key.move_to_end(key)
        return s

    def __len__(self) -> int:
        return len(self._by_key)
