#!/usr/bin/env python3
"""B3.1 — staleness-policy SAFETY replay for retroactive context retirement. ZERO Claude quota.

B3.0 gave the CEILING and flagged that its double digits ride the speculative ABANDONED TAIL (retire
an object once its key is last-touched). This asks the B2.3 question — *would retiring it ever break a
later step?* — generalized from files to all object kinds, and swept over a staleness lag L (retire at
last_touch + L turns).

The structural reason retroactive should be far safer than B2.3's prospective compaction (which forced
a re-read on 78% of edits): an Edit is itself a touch, so the file stays resident THROUGH its edit — a
retired object is never one the agent is about to edit. The only residual risk is the agent RE-NEEDING
a retired object's content afterwards. Two mechanical break signals (conservative — overcount unsafe):

  D0  RE-REFERENCE: a later tool call (turn > retire) whose input names the retired object's file path
      — the agent returned to that file via any tool (cat, grep, re-open at a new offset). Not caught
      by exact supersession keys, so a genuine miss.
  D1  CONTENT-REUSE: a later Edit whose old_string shares a distinctive source line with the retired
      object's content — the agent edited code it last saw in the retired object.

An object is UNSAFE-to-retire at lag L if D0 or D1 fires after its retire turn. SAFE fraction and the
token NET counting ONLY safe retirements are reported per L; supersession retirements are provably safe
(replaced at that turn) and always counted. Superseded/tail split and obsolescence come from B3.0.
"""
from __future__ import annotations

import json
import os
import re

from contextruntime.reducers.base import tokens as _tok
from corpus.b3_context_retirement import _obj_key, _norm_path, assign_obsolescence
from corpus.edit_recall_replay import _EDIT_TOOLS
from corpus.transcript_util import merged_records

_READ_TOOLS = {"Read", "NotebookRead"}
_KEYED_TOOLS = {"Grep", "Glob", "Bash"}
_LINENO = re.compile(r"^\s*\d+\t")
_MULTISPACE = re.compile(r"\s+")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _distinctive_lines(text: str, *, minchars: int = 20) -> frozenset:
    """Stripped source lines with >= minchars non-space characters — distinctive enough that a shared
    line signals genuine content reuse, not an incidental `return None`."""
    out = set()
    for ln in (text or "").splitlines():
        s = _LINENO.sub("", ln).strip()
        if len(_MULTISPACE.sub("", s)) >= minchars:
            out.add(s)
    return frozenset(out)


def parse_for_safety(transcript_path: str):
    """One pass. Returns (objects, edits, inputs_by_turn, T, usage). Each object keeps precomputed
    distinctive lines (not raw content) + its path; inputs_by_turn[t] = concatenated tool-use input
    strings at turn t (for D0 path re-reference)."""
    uses = {}
    objects, edits = [], []
    inputs_by_turn = {}
    usage = {"cache_read": 0, "cache_creation": 0, "input": 0, "output": 0}
    turn = 0
    for rec in merged_records(transcript_path):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            if msg.get("usage"):
                u = msg["usage"]
                turn += 1
                usage["cache_read"] += u.get("cache_read_input_tokens", 0)
                usage["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                usage["input"] += u.get("input_tokens", 0)
                usage["output"] += u.get("output_tokens", 0)
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                name, inp = b.get("name", ""), (b.get("input") or {})
                if name in _READ_TOOLS or name in _EDIT_TOOLS or name in _KEYED_TOOLS:
                    path = _norm_path(inp.get("file_path") or inp.get("notebook_path") or "")
                    uses[b.get("id")] = (name, _obj_key(name, inp), turn, path)
                    inputs_by_turn.setdefault(turn, []).append(json.dumps(inp))
                    if name in _EDIT_TOOLS:
                        if name == "MultiEdit":
                            for e in (inp.get("edits") or []):
                                if e.get("old_string"):
                                    edits.append((turn, path, e["old_string"]))
                        elif inp.get("old_string"):
                            edits.append((turn, path, inp["old_string"]))
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    ref = uses.get(b.get("tool_use_id"))
                    if ref:
                        name, key, tturn, path = ref
                        txt = _text(b.get("content"))
                        objects.append({"turn": tturn, "name": name, "key": key, "path": path,
                                        "size": _tok(txt), "dlines": _distinctive_lines(txt)})
    usage["T_total"] = sum(v for k, v in usage.items() if k != "T_total")
    inputs_by_turn = {t: "\n".join(v) for t, v in inputs_by_turn.items()}
    return objects, edits, inputs_by_turn, max(turn, 1), usage


def staleness_safety(objects, edits, inputs_by_turn, T, *, lag: int, d1_minchars: int = 20):
    """Retire each TAIL object at last_touch + lag; flag D0/D1 breaks after the retire turn. Returns
    counts + token NET counting only SAFE retirements (supersession always safe)."""
    assign_obsolescence(objects, T)
    edits_dl = sorted((et, _distinctive_lines(es, minchars=d1_minchars)) for et, ep, es in edits)
    inputs_sorted = sorted(inputs_by_turn.items())       # (turn, str)

    superseded_gross = sum(o["size"] * max(T - o["obsolete_turn"], 0)
                           for o in objects if o["obsolete_turn"] is not None)
    tails = [o for o in objects if o["tail_turn"] is not None]
    tail_gross = safe_gross = 0
    n_safe = n_d0 = n_d1 = n_retired = 0
    for o in tails:
        r = o["tail_turn"] + lag
        if r >= T:
            continue                                     # not retired within the session ⇒ no saving, no risk
        n_retired += 1
        g = o["size"] * (T - r)
        tail_gross += g
        d0 = bool(o["path"]) and any(o["path"] in s for t, s in inputs_sorted if t > r)
        d1 = False
        if o["dlines"]:
            for et, edl in edits_dl:
                if et > r and o["dlines"] & edl:
                    d1 = True
                    break
        if d0 or d1:
            n_d0 += int(d0)
            n_d1 += int(d1)
        else:
            n_safe += 1
            safe_gross += g
    return {"lag": lag, "n_tail_retired": n_retired, "n_safe": n_safe,
            "n_unsafe_d0_reref": n_d0, "n_unsafe_d1_reuse": n_d1,
            "safe_fraction": round(n_safe / n_retired, 4) if n_retired else None,
            "superseded_gross": round(superseded_gross), "tail_gross": round(tail_gross),
            "safe_tail_gross": round(safe_gross),
            "safe_total_gross": round(superseded_gross + safe_gross)}


def analyze_safety(transcript_path, *, lags=(0, 3, 5, 10, 20)):
    objects, edits, inputs_by_turn, T, usage = parse_for_safety(transcript_path)
    tt = usage["T_total"] or 0
    rows = []
    for L in lags:
        s = staleness_safety(objects, edits, inputs_by_turn, T, lag=L)
        s["safe_total_pct_T_total"] = round(s["safe_total_gross"] / tt * 100, 2) if tt else None
        s["tail_pct_T_total"] = round((s["superseded_gross"] + s["tail_gross"]) / tt * 100, 2) if tt else None
        rows.append(s)
    multiwindow = (usage["cache_read"] / T) > 210_000 if T else False
    return {"turns": T, "T_total": tt, "multiwindow": multiwindow, "by_lag": rows,
            "transcript": transcript_path}


_BUCKETS = ((0, 100), (100, 10 ** 9))


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 3) if xs else None


def run_safety(results_json, *, extra_transcripts=(), lags=(0, 3, 5, 10, 20)):
    res = json.load(open(results_json))
    sessions, seen = [], set()
    for key, m in res.items():
        if not isinstance(m, dict) or "error" in m:
            continue
        tp = m.get("transcript")
        if tp and os.path.exists(tp) and tp not in seen:
            seen.add(tp)
            sessions.append(analyze_safety(tp, lags=lags))
    extras = [analyze_safety(tp, lags=lags) for tp in extra_transcripts if tp and os.path.exists(tp)]
    single = [s for s in sessions if not s["multiwindow"]]
    # aggregate safe_fraction + safe NET per lag, and stratify short vs long
    agg = []
    for i, L in enumerate(lags):
        agg.append({"lag": L,
                    "safe_fraction": _mean([s["by_lag"][i]["safe_fraction"] for s in single]),
                    "safe_total_pct_T_total": _mean([s["by_lag"][i]["safe_total_pct_T_total"] for s in single]),
                    "unsafe_pct_T_total_gap": _mean([(s["by_lag"][i]["tail_pct_T_total"] or 0)
                                                     - (s["by_lag"][i]["safe_total_pct_T_total"] or 0) for s in single])})
    strata = []
    for lo, hi in _BUCKETS:
        sel = [s for s in single if lo <= s["turns"] < hi]
        if not sel:
            continue
        strata.append({"bucket": f"{lo}-{hi if hi < 10 ** 8 else '+'}", "n": len(sel),
                       "by_lag": [{"lag": L,
                                   "safe_fraction": _mean([s["by_lag"][i]["safe_fraction"] for s in sel]),
                                   "safe_total_pct_T_total": _mean([s["by_lag"][i]["safe_total_pct_T_total"] for s in sel])}
                                  for i, L in enumerate(lags)]})
    return {"n_sessions": len(sessions), "single_window_n": len(single), "lags": list(lags),
            "aggregate_by_lag": agg, "strata": strata, "extras": extras}


def _main(argv):
    extras = argv[2:] if len(argv) > 2 else []
    out = run_safety(argv[1], extra_transcripts=extras)
    print("=== B3.1 staleness-SAFETY replay (offline, zero-quota) ===")
    print(f"  {out['single_window_n']} single-window sessions\n")
    print(f"  {'lag':>4} {'safe_frac':>10} {'safeNET%T':>10} {'unsafe_gap%':>12}")
    for r in out["aggregate_by_lag"]:
        print(f"  {r['lag']:>4} {r['safe_fraction']:>10} {r['safe_total_pct_T_total']:>10} {r['unsafe_pct_T_total_gap']:>12}")
    for st in out["strata"]:
        print(f"\n  [{st['bucket']} turns, n={st['n']}]")
        for r in st["by_lag"]:
            print(f"    lag {r['lag']:>2}: safe_frac={r['safe_fraction']}  safeNET={r['safe_total_pct_T_total']}%")
    for e in out["extras"]:
        mw = " (MULTI-WINDOW: overstated)" if e["multiwindow"] else ""
        r = e["by_lag"][2]
        print(f"\n  extra {e['turns']}t @lag{r['lag']}: safe_frac={r['safe_fraction']} safeNET={r['safe_total_pct_T_total']}%{mw}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
