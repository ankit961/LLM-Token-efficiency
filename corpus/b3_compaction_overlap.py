#!/usr/bin/env python3
"""B3.2 — native-/compact OVERLAP model. ZERO Claude quota. No live run.

B3.1 showed retroactive retirement is ~95-99% safe and, on SINGLE-WINDOW sessions, purely additive
(native /compact never fires there). This models the one open piece: on LONG sessions where native
compaction DOES fire, how much of B3's saving does native already capture, and what does B3 uniquely add?

Native /compact is a BLUNT, LOSSY reset: at the context limit it evicts the ENTIRE prefix (dead AND
live objects) into a summary. B3 is a SURGICAL, LOSSLESS trickle: it retires only provably-dead/safe
objects continuously. Two consequences, modelled separately:

  REDUNDANCY (Model B, on real reset boundaries): once native resets at boundary K, everything B3 would
  have retired before K is gone anyway. So B3's UNIQUE token-turn saving = only what it retires inside
  each inter-reset window before that window's reset. B3's standalone % (B3.0/B3.1) OVERSTATES its
  marginal value by whatever native already evicts.

  DEFERRAL (Model A, parametric over a context threshold θ): B3 removing dead weight keeps the resident
  prefix smaller, so it crosses θ LATER — or never. Every avoided/deferred crossing is a lossy
  summarization B3 replaces with lossless retirement. This is the value native CANNOT provide (native
  is the thing being deferred). Measured on the real pre-crossing trajectory — no post-reset modelling.

Retirement policy here matches B3.1's safe realizable set: superseded at obsolete_turn, tails at
tail_turn + LAG (default 5). (Unsafe ~3% not filtered — immaterial to the resident-trajectory shape.)
"""
from __future__ import annotations

import json
import os

from contextruntime.reducers.base import tokens as _tok
from corpus.b3_context_retirement import _obj_key, _norm_path, assign_obsolescence
from corpus.transcript_util import merged_records

_READ = {"Read", "NotebookRead"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_KEYED = {"Grep", "Glob", "Bash"}
LAG = 5


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def parse_trajectory(transcript_path):
    """Returns (objects, cr_by_turn, T). cr_by_turn[t] = real cache_read at turn t = resident prefix
    size that turn. objects = {turn, key, size} for every tool result."""
    uses, objects, cr = {}, [], {}
    turn = 0
    for rec in merged_records(transcript_path):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            if msg.get("usage"):
                turn += 1
                cr[turn] = msg["usage"].get("cache_read_input_tokens", 0)
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name = b.get("name", "")
                    if name in _READ or name in _EDIT or name in _KEYED:
                        uses[b.get("id")] = (name, _obj_key(name, b.get("input") or {}), turn)
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    ref = uses.get(b.get("tool_use_id"))
                    if ref:
                        name, key, tturn = ref
                        objects.append({"turn": tturn, "key": key, "size": _tok(_text(b.get("content")))})
    return objects, cr, max(turn, 1)


def _retire_turns(objects, T, lag=LAG):
    assign_obsolescence(objects, T)
    r = {}
    for o in objects:
        if o["obsolete_turn"] is not None:
            r[id(o)] = o["obsolete_turn"]
        elif o["tail_turn"] is not None:
            r[id(o)] = o["tail_turn"] + lag
    return r


def _retired_cumulative(objects, retire, T):
    """cum[t] = Σ size of objects B3 has retired by turn t (monotonic non-decreasing)."""
    delta = [0] * (T + 2)
    for o in objects:
        rt = retire.get(id(o))
        if rt is not None and rt <= T:
            delta[rt] += o["size"]
    cum, run = [0] * (T + 1), 0
    for t in range(1, T + 1):
        run += delta[t]
        cum[t] = run
    return cum


def detect_boundaries(cr, T, *, drop=0.6, floor=50_000):
    """Native reset turns: cache_read falls below `drop`× the prior turn from a substantial (>floor)
    level — the prefix was summarized/reset."""
    b = []
    for t in range(2, T + 1):
        if cr.get(t - 1, 0) > floor and cr.get(t, 0) < cr.get(t - 1, 0) * drop:
            b.append(t)
    return b


def deferral_model(objects, cr, T, thetas, *, lag=LAG):
    """Model A: at each context threshold θ, does B3 avoid or defer the FIRST crossing? Uses only the
    real pre-crossing trajectory (no post-reset modelling)."""
    retire = _retire_turns(objects, T, lag=lag)
    cum = _retired_cumulative(objects, retire, T)
    resident_b3 = {t: max(cr.get(t, 0) - cum[t], 0) for t in range(1, T + 1)}
    peak_native = max((cr.get(t, 0) for t in range(1, T + 1)), default=0)
    peak_b3 = max((resident_b3[t] for t in range(1, T + 1)), default=0)
    rows = []
    for th in thetas:
        kn = next((t for t in range(1, T + 1) if cr.get(t, 0) > th), None)
        kb = next((t for t in range(1, T + 1) if resident_b3[t] > th), None)
        rows.append({"theta": th, "would_compact": kn is not None,
                     "K_native": kn, "K_b3": kb,
                     "avoided": kn is not None and kb is None,
                     "deferral_turns": ((kb or T) - kn) if kn is not None else None})
    return {"peak_native": peak_native, "peak_b3": peak_b3,
            "peak_reduction_pct": round((peak_native - peak_b3) / peak_native * 100, 2) if peak_native else None,
            "by_theta": rows}


def redundancy_model(objects, cr, T, *, lag=LAG):
    """Model B: on the session's REAL reset boundaries, B3's UNIQUE token-turn saving = only what it
    retires before each window's reset. Returns unique / standalone."""
    retire = _retire_turns(objects, T, lag=lag)
    boundaries = detect_boundaries(cr, T)
    standalone = unique = 0
    for o in objects:
        rt = retire.get(id(o))
        if rt is None or rt >= T:
            continue
        standalone += o["size"] * (T - rt)
        nb = next((k for k in boundaries if k > o["turn"]), T)   # native evicts this object at nb
        unique += o["size"] * max(min(nb, T) - rt, 0)            # B3 credit only before nb
    return {"n_boundaries": len(boundaries), "standalone_tokturns": round(standalone),
            "unique_tokturns": round(unique),
            "unique_fraction": round(unique / standalone, 4) if standalone else None}


_THETAS = (80_000, 120_000, 160_000, 190_000)


def analyze_overlap(transcript_path, *, thetas=_THETAS, lag=LAG):
    objects, cr, T = parse_trajectory(transcript_path)
    return {"turns": T, "peak_cache_read": max(cr.values(), default=0),
            "deferral": deferral_model(objects, cr, T, thetas, lag=lag),
            "redundancy": redundancy_model(objects, cr, T, lag=lag),
            "transcript": transcript_path}


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


def run_overlap(results_json, *, extra_transcripts=(), thetas=_THETAS, lag=LAG):
    res = json.load(open(results_json))
    sessions, seen = [], set()
    for _k, m in res.items():
        if not isinstance(m, dict) or "error" in m:
            continue
        tp = m.get("transcript")
        if tp and os.path.exists(tp) and tp not in seen:
            seen.add(tp)
            sessions.append(analyze_overlap(tp, thetas=thetas, lag=lag))
    extras = [analyze_overlap(tp, thetas=thetas, lag=lag) for tp in extra_transcripts if tp and os.path.exists(tp)]
    # aggregate deferral per theta over sessions that WOULD compact at that theta
    agg = []
    for i, th in enumerate(thetas):
        would = [s for s in sessions if s["deferral"]["by_theta"][i]["would_compact"]]
        avoided = [s for s in would if s["deferral"]["by_theta"][i]["avoided"]]
        agg.append({"theta": th, "n_would_compact": len(would),
                    "n_fully_avoided": len(avoided),
                    "avoided_frac": round(len(avoided) / len(would), 3) if would else None,
                    "mean_deferral_turns": _mean([s["deferral"]["by_theta"][i]["deferral_turns"] for s in would])})
    return {"n_sessions": len(sessions), "lag": lag, "thetas": list(thetas),
            "mean_peak_reduction_pct": _mean([s["deferral"]["peak_reduction_pct"] for s in sessions]),
            "deferral_by_theta": agg, "extras": extras, "sessions_peak_cr": [s["peak_cache_read"] for s in sessions]}


def _main(argv):
    extras = argv[2:] if len(argv) > 2 else []
    out = run_overlap(argv[1], extra_transcripts=extras)
    print("=== B3.2 native-/compact OVERLAP model (offline, zero-quota) ===")
    print(f"  {out['n_sessions']} sessions; mean peak-resident reduction from B3 = {out['mean_peak_reduction_pct']}%")
    peaks = sorted(out["sessions_peak_cr"])
    print(f"  session peak cache_read: p50={peaks[len(peaks) // 2]:,} max={max(peaks):,}\n")
    print("  DEFERRAL (Model A) — among sessions that would compact at θ:")
    print(f"    {'θ':>8} {'nWouldCompact':>14} {'fullyAvoided':>13} {'meanDeferTurns':>15}")
    for r in out["deferral_by_theta"]:
        print(f"    {r['theta']:>8,} {r['n_would_compact']:>14} {str(r['avoided_frac']):>13} {str(r['mean_deferral_turns']):>15}")
    for e in out["extras"]:
        rd = e["redundancy"]
        print(f"\n  REDUNDANCY (Model B) on GIANT {e['turns']}t: {rd['n_boundaries']} real resets → "
              f"B3 unique saving = {rd['unique_fraction']} of standalone "
              f"(peak-reduction {e['deferral']['peak_reduction_pct']}%)")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
