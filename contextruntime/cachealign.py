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
  when pending savings clear the break-even: read_mult · pending_tokens · E[remaining calls] ≥
  (write_mult − read_mult) · suffix_tokens.

  PERSISTENT once fired: a fired mutation re-applies to EVERY subsequent request, so the byte
  stream stays prefix-stable between fires and only NEW mutations ever invalidate. (The B6
  gateway applied retirements only on batch-boundary requests and let history snap back in
  between — each boundary paid the invalidation again and the residency saving lasted one
  request.)

The scheduler holds per-process state (the proxy serves one session); it decides WHEN, never
WHAT — safety stays entirely in RetirementPlanner.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

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

    def decide(self, pending: List[Tuple[str, int, int]], suffix_tokens_est: int,
               *, now_ts: Optional[float] = None) -> FireDecision:
        """pending: (object_key, turn, tokens_est) for retirable objects NOT yet fired."""
        now = time.time() if now_ts is None else now_ts
        gap = None if self.last_request_ts is None else now - self.last_request_ts
        self.last_request_ts = now
        pending_tokens = sum(t for _, _, t in pending)
        if self.mode == "off":
            return FireDecision(True, "align-off", gap, pending_tokens, suffix_tokens_est)
        if gap is None:
            return FireDecision(True, "cold-start", gap, pending_tokens, suffix_tokens_est)
        if gap > self.ttl_s:
            return FireDecision(True, "ttl-gap", gap, pending_tokens, suffix_tokens_est)
        if self.mode == "gated" and pending_tokens:
            gain = self.read_mult * pending_tokens * self.e_remaining
            cost = (self.write_mult - self.read_mult) * max(suffix_tokens_est, 0)
            if gain >= cost:
                return FireDecision(True, "break-even", gap, pending_tokens, suffix_tokens_est)
        return FireDecision(False, "hold", gap, pending_tokens, suffix_tokens_est)

    def commit(self, keys, n_turns: int) -> None:
        """Record a fire: these mutations now re-apply to every subsequent request."""
        self.fired_keys.update(keys)
        self.strip_frontier = max(self.strip_frontier, n_turns - 1)

    @classmethod
    def from_profile(cls, mode: str, profile) -> "CacheAlignedScheduler":
        """Build the scheduler from a `contextruntime.providers.ProviderProfile` — the ONLY
        provider-specific inputs the break-even rule needs. anthropic-1h ⇒ break-even 19 reads
        (hold on short sessions); free-write providers ⇒ break-even 1 (fire almost always)."""
        return cls(mode=mode, ttl_s=profile.ttl_s, write_mult=profile.write_mult,
                   read_mult=profile.read_mult)
