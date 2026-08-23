#!/usr/bin/env python3
"""B5.2 Stage A — EXACT joint counterfactual replay of the lever stack. ZERO model quota.

The path-to-50 arithmetic multiplied corpus-average reductions, but the levers are not independent:
collapse removes calls B3 would have saved residency on; prefix hygiene changes the value of every
avoided call; collapse shortens how long tool results stay resident; thinking-GC varies by call depth.
This replay applies every intervention to the SAME per-call trajectory, so overlap is exact:

    L0 baseline
    L1 prefix hygiene            P_t − ΔF                      (ΔF = env's measured achievable cut)
    L2 + D0/D1 collapse          drop the avoided calls of evidence-gated runs (oracle)
    L3 + B3 retirement           P_t − Σ size of SAFE objects retired before t (per-object verdicts)
    L4 + thinking-GC (measured)  P_t − Σ think_s for s ≤ t−2   (keep-1; think from THIS session)

Per-call thinking opportunity is OBSERVED, not assumed: think_t = max(output_tokens_t −
FACTOR × cl100k(visible output_t), 0) with the Claude request-accounting factor 1.74 applied — which
CORRECTS the earlier 11.3% thinking share (computed without the factor, hence overstated). The
factor-1.0 variant is reported as an upper bound.

The three P_t slices removed (fixed schemas ΔF, retired tool outputs, retained thinking) are disjoint
components of the prefix, so per-call subtraction is valid; any clamp at zero is counted and reported.

Primary output per environment: exact_joint_reduction vs the multiplicative approximation built from
each lever's SOLO exact reduction on the same sessions — if they differ by >2pp, path-to-50 claims
must be updated. All numbers are counterfactual opportunities, not live realized savings.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from corpus.transcript_util import merged_records
from corpus.call_collapse_oracle import analyze_session as oracle_session, tok
from corpus.b3_staleness_safety import parse_for_safety, per_object_safety

FACTOR = 1.74                     # Claude request-accounting factor (see prefix-doctor findings)
B3_LAG = 5


def visible_out_per_call(transcript_path):
    """cl100k tokens of each call's VISIBLE assistant output (text + tool_use inputs) — the rest of
    output_tokens is retained thinking (invisible; signature-only on display-omitted models)."""
    vis = []
    for rec in merged_records(transcript_path):
        if rec.get("isSidechain"):
            continue
        m = rec.get("message") or {}
        c = m.get("content")
        if rec.get("type") == "assistant" and isinstance(c, list) and m.get("usage"):
            v = 0
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    v += tok(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    v += tok(json.dumps(b.get("input") or {}))
            vis.append(v)
    return vis


def session_joint(transcript_path, *, prefix_frac, cli_meta=None, factor=FACTOR):
    """Exact per-session replay. Returns per-level Σ P, calls, Σ out, peak P, plus solo-lever sums."""
    orc = oracle_session(transcript_path, cli_meta=cli_meta)
    calls = None
    # re-parse the calls exactly as the oracle did (it doesn't return them)
    from corpus.call_collapse_oracle import parse_session
    calls = parse_session(transcript_path)
    N = len(calls)
    if N == 0:
        return None
    # avoided calls: calls 2..n of every evidence-gated D0/D1 multi-call run
    avoided = set()
    for r in orc["runs"]:
        if r["n_calls"] >= 2 and r["run_class"] in ("D0", "D0D1") and (
                r["retention"] is None or r["retention"].get("next_action_ok")):
            avoided.update(range(r["start_call"] + 1, r["end_call"] + 1))
    # B3 safe retirements (per-object verdicts, immediate at retire turn)
    objs, edits, inputs_by_turn, T_b3, _u = parse_for_safety(transcript_path)
    b3_ok = (T_b3 == N)
    retire = [(rt, o["size"]) for (o, rt, safe) in
              per_object_safety(objs, edits, inputs_by_turn, T_b3, lag=B3_LAG) if safe] if b3_ok else []
    retire.sort()
    # thinking opportunity per call (measured from THIS session)
    vis = visible_out_per_call(transcript_path)
    think = [max(c["out_tokens"] - round(factor * v), 0) for c, v in zip(calls, vis)] if len(vis) == N else [0] * N
    # per-call cumulative reductions
    H = round(prefix_frac * calls[0]["P"])
    cumret, ci = [0] * (N + 1), 0
    run = 0
    for t in range(1, N + 1):
        while ci < len(retire) and retire[ci][0] < t:
            run += retire[ci][1]
            ci += 1
        cumret[t] = run
    cumthink = [0] * (N + 1)
    for t in range(1, N + 1):
        # keep-1: at call t the prior assistant messages are 1..t-1; the LATEST (t-1) keeps its
        # thinking, so the strippable set is calls 1..t-2  (0-indexed think[0..t-3])
        cumthink[t] = cumthink[t - 1] + (think[t - 3] if t >= 3 else 0)

    clamps = Counter()

    def px(c, *reds):
        v = c["P"] - sum(reds)
        if v < 0:
            clamps["negative"] += 1
            return 0
        return v

    lvl = {k: {"P": 0, "calls": 0, "out": 0, "peak": 0} for k in ("L0", "L1", "L2", "L3", "L4")}
    solo = {k: 0 for k in ("prefix", "collapse", "b3", "think")}
    for c in calls:
        t = c["idx"]
        lvl["L0"]["P"] += c["P"]
        lvl["L0"]["out"] += c["out_tokens"]
        lvl["L0"]["peak"] = max(lvl["L0"]["peak"], c["P"])
        lvl["L1"]["P"] += px(c, H)
        lvl["L1"]["out"] += c["out_tokens"]
        lvl["L1"]["peak"] = max(lvl["L1"]["peak"], px(c, H))
        solo["prefix"] += px(c, H)
        solo["b3"] += px(c, cumret[t])
        solo["think"] += px(c, cumthink[t])
        if t not in avoided:
            solo["collapse"] += c["P"]
            for k, reds in (("L2", (H,)), ("L3", (H, cumret[t])), ("L4", (H, cumret[t], cumthink[t]))):
                v = px(c, *reds)
                lvl[k]["P"] += v
                lvl[k]["out"] += c["out_tokens"]
                lvl[k]["peak"] = max(lvl[k]["peak"], v)
                lvl[k]["calls"] += 1
    lvl["L0"]["calls"] = lvl["L1"]["calls"] = N
    return {"transcript": transcript_path, "N": N, "avoided": len(avoided), "b3_turn_axis_ok": b3_ok,
            "startup_P": calls[0]["P"], "H": H, "clamps": dict(clamps),
            "think_total_measured": sum(think), "levels": lvl, "solo": solo}


def aggregate(rows, label):
    tot = lambda k, f: sum(r["levels"][k][f] for r in rows)          # noqa: E731
    base_P = tot("L0", "P")
    out = {"environment": label, "sessions": len(rows),
           "b3_turn_axis_mismatches": sum(1 for r in rows if not r["b3_turn_axis_ok"]),
           "clamped_calls": sum(r["clamps"].get("negative", 0) for r in rows)}
    for k in ("L0", "L1", "L2", "L3", "L4"):
        out[k] = {"sum_P": tot(k, "P"), "calls": tot(k, "calls"), "sum_out": tot(k, "out"),
                  "reduction_pct": round(100 * (1 - tot(k, "P") / base_P), 2) if base_P else None,
                  "peak_P_mean": round(sum(r["levels"][k]["peak"] for r in rows) / len(rows)) if rows else None}
    # multiplicative approximation from SOLO exact reductions on the same sessions
    r_solo = {k: 1 - sum(r["solo"][k] for r in rows) / base_P for k in ("prefix", "collapse", "b3", "think")}
    mult = 1.0
    for v in r_solo.values():
        mult *= (1 - v)
    out["solo_reductions_pct"] = {k: round(100 * v, 2) for k, v in r_solo.items()}
    out["multiplicative_approx_pct"] = round(100 * (1 - mult), 2)
    out["exact_joint_pct"] = out["L4"]["reduction_pct"]
    out["approx_minus_exact_pp"] = round(out["multiplicative_approx_pct"] - out["exact_joint_pct"], 2)
    return out


def run_stage_a(results_json=None, *, prefix_frac, label, transcripts=None, metas=None):
    rows = []
    if results_json:
        res = json.load(open(results_json))
        seen = set()
        for key, m in res.items():
            if not isinstance(m, dict) or "error" in m:
                continue
            tp = m.get("transcript")
            if tp and os.path.exists(tp) and tp not in seen:
                seen.add(tp)
                r = session_joint(tp, prefix_frac=prefix_frac, cli_meta=m)
                if r:
                    rows.append(r)
    else:
        for tp in transcripts or []:
            r = session_joint(tp, prefix_frac=prefix_frac)
            if r:
                rows.append(r)
    return {"rows": rows, "aggregate": aggregate(rows, label)}
