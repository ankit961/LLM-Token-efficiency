"""B7 — prefix-cache cost model for cache-aligned retirement.

WHY: B6 measured that the lifetime levers (B3 retirement + thinking-GC) cut live input residency
41.5% but dollars only ~2.5%: every history mutation invalidates the incremental prompt cache from
the edit point, and the suffix re-bills as cache CREATION. A zero-quota capture of a live request
shows this client requests `cache_control: {type: ephemeral, ttl: "1h"}` — so creation bills at
the 1-hour tier's **2.0×** base input price (not the 5-minute tier's 1.25×), and reads at 0.1×.
At those prices B6's list-price delta reconstructs to ≈ −2.8%, matching the CLI's −2.5%.

THE MODEL (message-granularity, API-token units — no text estimation on the critical path):

  Observed per call t:  P_t = cache_read_t + cache_creation_t + input_t   (API ground truth)
  Breakpoint convention (captured live): two breakpoints close the fixed prefix (tools+system),
  one rides the LAST message of every request. Hence after call s completes, a cached prefix of
  size ≈ P_s exists (TTL 1h, refreshed on use).

  Append-only call t:            read_t = P_{t-1},          creation_t = P_t − P_{t-1}
  Call t editing history whose earliest edited object lives at call c:
                                 read_t = P*_{c-1}  (largest surviving breakpoint ≤ edit point),
                                 creation_t = P'_t − read_t          (P' = post-mutation totals)
  TTL expiry (gap > ttl):        read_t = fixed-prefix share only (system+tools refreshed by the
                                 harness's own keep-alive is NOT assumed) → suffix re-creates.

The simulator tracks the set of live breakpoint prefixes with TTLs and answers (read, creation)
per request; policies differ only in WHEN mutations fire, so the same simulator prices native,
B6-unaligned, and cache-aligned variants of the same session.

Prices are expressed in base-input-token equivalents (BITE): read 0.1, 1h-write 2.0, 5m-write
1.25, uncached input 1.0, output 5.0 (sonnet list ratio $15/$3). Dollars = BITE × $3/M (sonnet).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

READ_MULT = 0.1
WRITE_MULT_1H = 2.0
WRITE_MULT_5M = 1.25
OUT_MULT = 5.0                     # sonnet $15 out / $3 in
USD_PER_MTOK_IN = 3.0
TTL_1H = 3600.0


@dataclass
class CallRecord:
    """One real API call, from the transcript (requestId-merged, sidechains excluded)."""
    P: int                         # read + creation + input (API-observed total prompt tokens)
    read: int
    creation: int
    input: int
    out: int
    ts: float                      # request wall-clock (epoch seconds)


def extract_calls(transcript_path: str) -> List[CallRecord]:
    from corpus.transcript_util import merged_records
    from datetime import datetime
    calls = []
    for rec in merged_records(transcript_path):
        if rec.get("isSidechain"):
            continue
        m = rec.get("message") or {}
        u = m.get("usage")
        if rec.get("type") == "assistant" and u and isinstance(m.get("content"), list):
            ts = 0.0
            raw = rec.get("timestamp")
            if raw:
                try:
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = 0.0
            rd = u.get("cache_read_input_tokens", 0)
            cr = u.get("cache_creation_input_tokens", 0)
            inp = u.get("input_tokens", 0)
            if rd + cr + inp == 0:
                continue                                   # trailing stub records carry empty usage
            calls.append(CallRecord(P=rd + cr + inp, read=rd, creation=cr, input=inp,
                                    out=u.get("output_tokens", 0), ts=ts))
    return calls


@dataclass
class PrefixCacheSim:
    """Cached prefix EXTENTS with TTL. Validated against live behavior: the API serves partial
    interior hits — a request whose first X tokens match a cached extent reads X from cache even
    when the divergence point lies inside the extent (observed live: keep-1 thinking strips cost
    ~1 turn of re-creation, and a deep retirement batch re-creates exactly the suffix from the
    edit point, never the whole prompt). Hence: read = min(unchanged_prefix, largest alive
    extent); an edit drops extents that reach past the divergence (their tail no longer matches
    this stream). Sizes are compared on the caller's token axis — feed unchanged-prefix sizes
    measured the same way as totals (both observed P, or both counterfactual P')."""
    ttl_s: float = TTL_1H
    write_mult: float = WRITE_MULT_1H
    prefixes: List[List[float]] = field(default_factory=list)   # [extent_size, expires_at]

    def _expire(self, ts: float) -> None:
        self.prefixes = [p for p in self.prefixes if p[1] > ts]

    def request(self, ts: float, total_tokens: int, unchanged_prefix_tokens: Optional[int] = None):
        """unchanged_prefix_tokens: how much of THIS request's byte stream is identical to history
        (None = append-only, everything previously sent is unchanged)."""
        self._expire(ts)
        limit = total_tokens if unchanged_prefix_tokens is None else min(unchanged_prefix_tokens, total_tokens)
        extent = max((p[0] for p in self.prefixes), default=0)
        read = int(min(limit, extent))
        creation = max(total_tokens - read, 0)
        if unchanged_prefix_tokens is not None:                 # edited: stale tails can't hit again
            self.prefixes = [p for p in self.prefixes if p[0] <= limit]
        for p in self.prefixes:                                 # the served extent's TTL refreshes
            if p[0] <= read:
                p[1] = max(p[1], ts + self.ttl_s)
        self.prefixes.append([total_tokens, ts + self.ttl_s])
        return read, creation

    def cold(self, ts: float) -> bool:
        self._expire(ts)
        return not self.prefixes


def bite(read: int, creation: int, uncached: int, out: int, *, write_mult: float = WRITE_MULT_1H,
         read_mult: float = READ_MULT, out_mult: float = OUT_MULT) -> float:
    """Base-input-token-equivalent price of one call. The default constants are the live-validated
    anthropic-1h profile; pass another `contextruntime.providers.ProviderProfile`'s constants to
    price the same session under a different provider's cache economics."""
    return read_mult * read + write_mult * creation + 1.0 * uncached + out_mult * out


def bite_profile(read: int, creation: int, uncached: int, out: int, profile) -> float:
    return bite(read, creation, uncached, out, write_mult=profile.write_mult,
                read_mult=profile.read_mult, out_mult=profile.out_mult)


def usd(bite_total: float) -> float:
    return bite_total * USD_PER_MTOK_IN / 1e6


def session_bite(calls: List[CallRecord], *, write_mult: float = WRITE_MULT_1H) -> float:
    """Price a session exactly as observed (ground truth side of every comparison)."""
    return sum(bite(c.read, c.creation, c.input, c.out, write_mult=write_mult) for c in calls)


def calibrate_append_only(calls: List[CallRecord], *, warm_prefix: Optional[int] = None):
    """Test the model's append-only branch on an unmutated (native) session: predict
    read_t = P_{t-1}, creation_t = P_t − P_{t-1} and compare with observed sums. Returns the
    per-session relative errors — the honest quality bound quoted with every B7 number.

    warm_prefix: cached tokens already live when the session starts. The prompt cache is shared
    across sessions with byte-identical prefixes, so back-to-back sessions in one environment
    start with the fixed prefix (tools+system) warm — observed as read_1 > 0 on the very first
    call. Default: seed with the session's own observed read_1 (0 for a genuinely cold start)."""
    if len(calls) < 2:
        return None
    sim = PrefixCacheSim()
    t0 = calls[0].ts if calls[0].ts else 0.0
    seed = calls[0].read if warm_prefix is None else warm_prefix
    if seed:
        sim.prefixes.append([seed, t0 + sim.ttl_s])
    pred_read = pred_cre = 0
    obs_read = obs_cre = 0
    for c in calls:
        r, w = sim.request(c.ts if c.ts else 0.0, c.P, None)
        pred_read += r
        pred_cre += w
        obs_read += c.read
        obs_cre += c.creation
    return {
        "pred_read": pred_read, "obs_read": obs_read,
        "pred_creation": pred_cre, "obs_creation": obs_cre,
        "read_err_pct": 100.0 * (pred_read - obs_read) / obs_read if obs_read else None,
        "creation_err_pct": 100.0 * (pred_cre - obs_cre) / obs_cre if obs_cre else None,
    }


def load_b6_sessions(results_path: str, arm: str):
    """(instance_id, rep_key, transcript_path) triples for one arm of the frozen B6 artifact."""
    res = json.load(open(results_path))
    out = []
    for tid, arms in sorted(res["tasks"].items()):
        for key, rec in sorted(arms.items()):
            if key.startswith(arm) and rec.get("transcript"):
                out.append((tid, key, rec["transcript"]))
    return out
