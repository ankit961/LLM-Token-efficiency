#!/usr/bin/env python3
"""B3.0 — retroactive CONTEXT-RETIREMENT ceiling. ZERO Claude quota. No live transformer.

Generalizes B2.v2 `retroactive_replay` from FILES to all retirable CONTEXT OBJECTS: every tool result
(file read, grep, glob, bash, edit/write diff) is an object that enters the prefix at its turn and is
cache-READ every later turn until retired. The thesis (`Value(x)=Tokens(x)×RemainingTurns(x)`): stop
carrying an object once its useful lifetime is over.

Obsolescence triggers (turn at/after which an object is provably or plausibly dead weight):
  - SUPERSEDED (mechanical): a later object touches the same file path, or repeats the same
    grep/glob/bash key. The earlier view is stale from the later turn. Provably obsolete.
  - POST-EDIT stale (mechanical): a read of P superseded by a later Edit/Write to P (subsumed by
    same-path supersession — edits carry the path).
  - ABANDONED TAIL (speculative): the LAST object touching a path/key, never revisited. Assumed dead
    after its own turn. NOT provable (the agent may recall it), so reported separately.

Economics — the crux of why retroactive is hard, in TWO currencies:
  - RAW T_total tokens (context-pressure / turns-to-limit): retiring o saves size(o)·(T − retire_turn).
    A prefix REWRITE is ~free here: the invalidated suffix is counted once as cache_creation instead
    of cache_read that turn — same raw token count. So raw NET ≈ GROSS; batching barely matters.
  - COST ($, cache-weighted): cache_read is cheap (0.1x), cache_creation dear (1.25x 5-min / 2.0x
    1-hour). Retiring saves 0.1x·gross; each compaction event re-creates the invalidated suffix at
    (write−read)x once. COST NET = READ·gross − (WRITE−READ)·Σ_events suffix. Positive only when the
    session is long enough (gross ∝ T) and compaction is batched coarsely (few events). This is what
    B3.0 measures, stratified by length.

Preregistered gate is set in `docs/b3-findings.md` after the numbers are in; B3.0 STOPS at the offline
oracle ceiling — the live history transformer is not built until the NET ceiling justifies it.
"""
from __future__ import annotations

import json
import os
import re

from contextruntime.reducers.base import tokens as _tok
from corpus.transcript_util import merged_records

_READ_TOOLS = {"Read", "NotebookRead"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_KEYED_TOOLS = {"Grep", "Glob", "Bash"}                  # superseded only by an EXACT repeat
_LINENO = re.compile(r"^\s*\d+\t")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _norm_path(p) -> str:
    return os.path.normpath(str(p)).lstrip("./") if p else ""


def _obj_key(name: str, inp: dict) -> str:
    """Supersession key. File tools key on PATH (any later touch supersedes the earlier view);
    keyed tools key on their exact invocation (only an identical repeat supersedes)."""
    if name in _READ_TOOLS or name in _EDIT_TOOLS:
        return "path:" + _norm_path(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "Bash":
        return "bash:" + (inp.get("command") or "").strip()
    if name == "Grep":
        return "grep:" + json.dumps({k: inp.get(k) for k in ("pattern", "path", "glob", "type")}, sort_keys=True)
    if name == "Glob":
        return "glob:" + json.dumps({k: inp.get(k) for k in ("pattern", "path")}, sort_keys=True)
    return ""


def parse_context_objects(transcript_path: str):
    """Walk a transcript once. Returns (objects, total_turns, usage) where each object is
    {turn, name, key, size} for every tool result that becomes resident context, `usage` is the real
    per-turn token accounting, and total_turns counts assistant turns with a usage record."""
    uses = {}                                            # tool_use_id -> (name, key, turn)
    objects = []
    usage = {"cache_read": 0, "cache_creation": 0, "input": 0, "output": 0}
    turn = 0
    for rec in merged_records(transcript_path):
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            u = msg.get("usage")
            if u:
                turn += 1
                usage["cache_read"] += u.get("cache_read_input_tokens", 0)
                usage["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                usage["input"] += u.get("input_tokens", 0)
                usage["output"] += u.get("output_tokens", 0)
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name = b.get("name", "")
                    if name in _READ_TOOLS or name in _EDIT_TOOLS or name in _KEYED_TOOLS:
                        uses[b.get("id")] = (name, _obj_key(name, b.get("input") or {}), turn)
        if isinstance(content, list):                    # tool_results arrive in user turns
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    ref = uses.get(b.get("tool_use_id"))
                    if ref:
                        name, key, tturn = ref
                        objects.append({"turn": tturn, "name": name, "key": key,
                                        "size": _tok(_text(b.get("content")))})
    usage["T_total"] = sum(v for k, v in usage.items() if k != "T_total")
    return objects, max(turn, 1), usage


def assign_obsolescence(objects, total_turns):
    """Annotate each object with obsolete_turn (mechanical: superseded by a later same-key object) and
    tail_turn (speculative: last-touch of a key never revisited). Objects are in creation order."""
    # index occurrences per key
    by_key = {}
    for i, o in enumerate(objects):
        by_key.setdefault(o["key"], []).append(i)
    for o in objects:
        o["obsolete_turn"] = None
        o["tail_turn"] = None
    for key, idxs in by_key.items():
        if not key:
            continue
        for j, i in enumerate(idxs):
            if j + 1 < len(idxs):                        # a later object repeats this key
                objects[i]["obsolete_turn"] = objects[idxs[j + 1]]["turn"]
            else:                                        # last occurrence = abandoned tail
                objects[i]["tail_turn"] = objects[i]["turn"]
    return objects


# cache price multipliers relative to base input tokens (Anthropic prompt caching)
READ_MULT = 0.1
WRITE_MULT_5MIN = 1.25
WRITE_MULT_1H = 2.0


def _position(objects):
    """prefix position (creation order) -> used to size the invalidated suffix of a compaction."""
    return {id(o): i for i, o in enumerate(objects)}


def retirement_ceiling(objects, total_turns, usage, *, include_tail=False):
    """Oracle IMMEDIATE-retirement ceiling (retire each object the moment it goes obsolete). Reports
    raw token-turn saving and the peak/mean instantaneous occupancy relieved. This is the GROSS the
    cost model then charges rewrite against."""
    T = total_turns
    retire = []
    for o in objects:
        rt = o["obsolete_turn"]
        if rt is None and include_tail:
            rt = o["tail_turn"]
        if rt is not None:
            retire.append((o, rt))
    gross_tokturns = sum(o["size"] * max(T - rt, 0) for o, rt in retire)
    # instantaneous occupancy relieved per turn = obsolete-but-would-be-resident tokens
    relieved = [0] * (T + 2)
    resident_all = [0] * (T + 2)
    for o in objects:
        c = min(o["turn"], T)
        for t in range(c, T + 1):
            resident_all[t] += o["size"]
    for o, rt in retire:
        for t in range(min(rt, T), T + 1):
            relieved[t] += o["size"]
    peak_resident = max(resident_all[1:T + 1] or [0])
    mean_relief = sum(relieved[1:T + 1]) / T
    peak_relief = max(relieved[1:T + 1] or [0])
    cr = usage.get("cache_read", 0) or 0
    return {"total_turns": T, "n_objects": len(objects), "n_retired": len(retire),
            "gross_tokturns": round(gross_tokturns),
            "pct_of_cache_read": round(gross_tokturns / cr * 100, 2) if cr else None,
            "pct_of_T_total": round(gross_tokturns / usage["T_total"] * 100, 2) if usage.get("T_total") else None,
            "peak_resident_tokens": peak_resident,
            "peak_occupancy_relief_pct": round(peak_relief / peak_resident * 100, 2) if peak_resident else None,
            "mean_occupancy_relief_tokens": round(mean_relief)}


def simulate_batched(objects, total_turns, usage, *, policy="everyK", K=10, threshold=20000,
                     include_tail=False, write_mult=WRITE_MULT_5MIN):
    """Simulate batched compaction under a policy, charging the cache-rewrite premium. Returns realized
    gross (token-turns), rewrite tokens, and NET in both currencies.

    Rewrite model: a compaction at turn b that removes objects whose earliest prefix position is p_min
    invalidates every still-resident object at position > p_min — that suffix is re-created ONCE
    (cache_creation) instead of read. Raw T_total: ~0 added (creation replaces read 1:1 that turn).
    Cost: +(write_mult − READ_MULT)·suffix once; saving: READ_MULT·gross."""
    T = total_turns
    pos = _position(objects)
    order = sorted(objects, key=lambda o: pos[id(o)])
    obsolete = {}
    for o in objects:
        rt = o["obsolete_turn"]
        if rt is None and include_tail:
            rt = o["tail_turn"]
        if rt is not None:
            obsolete[id(o)] = rt
    # compaction turns
    if policy == "everyK":
        events = list(range(K, T + 1, K))
    elif policy == "once_end":
        events = [max(T - 1, 1)]
    elif policy == "threshold":
        events = []                                      # decided dynamically below
    else:
        raise ValueError(policy)

    retired = set()
    realized_gross = 0
    rewrite_tokens = 0
    n_events = 0

    def _do_compaction(b):
        nonlocal realized_gross, rewrite_tokens, n_events
        ready = [o for o in order if id(o) in obsolete and id(o) not in retired and obsolete[id(o)] <= b]
        if not ready:
            return
        p_min = min(pos[id(o)] for o in ready)
        for o in ready:
            realized_gross += o["size"] * max(T - b, 0)
            retired.add(id(o))
        # invalidated suffix = still-resident objects positioned after the earliest removal
        suffix = sum(o["size"] for o in order
                     if pos[id(o)] > p_min and id(o) not in retired and o["turn"] <= b)
        rewrite_tokens += suffix
        n_events += 1

    if policy == "threshold":
        acc = 0
        seen = set()
        for b in range(1, T + 1):
            for o in objects:
                if id(o) in obsolete and obsolete[id(o)] == b and id(o) not in seen:
                    acc += o["size"]
                    seen.add(id(o))
            if acc >= threshold:
                _do_compaction(b)
                acc = 0
        _do_compaction(T)                                # flush the tail
    else:
        for b in events:
            _do_compaction(b)

    read = READ_MULT
    cost_saving = read * realized_gross
    cost_rewrite = (write_mult - read) * rewrite_tokens
    cost_net = cost_saving - cost_rewrite
    tt = usage.get("T_total") or 0
    cr = usage.get("cache_read") or 0
    # cost baseline: what the whole session's cache traffic costs, weighted (read cheap, create dear)
    cost_baseline = read * cr + write_mult * (usage.get("cache_creation") or 0) + usage.get("input", 0) + usage.get("output", 0)
    return {"policy": policy, "K": K if policy == "everyK" else None,
            "threshold": threshold if policy == "threshold" else None,
            "n_events": n_events, "n_retired": len(retired),
            "realized_gross_tokturns": round(realized_gross),
            "rewrite_tokens": round(rewrite_tokens),
            "raw_net_pct_of_T_total": round(realized_gross / tt * 100, 2) if tt else None,
            "cost_saving": round(cost_saving), "cost_rewrite": round(cost_rewrite),
            "cost_net": round(cost_net),
            "cost_net_pct_of_baseline": round(cost_net / cost_baseline * 100, 2) if cost_baseline else None,
            "write_mult": write_mult}


def analyze_session(transcript_path, *, include_tail=True, batch_K=10):
    """Full B3 pass over one transcript → ceiling (mechanical + optionally tail) + batched cost view."""
    objs, T, usage = parse_context_objects(transcript_path)
    assign_obsolescence(objs, T)
    mech = retirement_ceiling(objs, T, usage, include_tail=False)
    full = retirement_ceiling(objs, T, usage, include_tail=include_tail)
    batch = simulate_batched(objs, T, usage, policy="everyK", K=batch_K, include_tail=include_tail)
    # multi-window flag: T_total far exceeds a single 200k window ⇒ native compaction already happened
    multiwindow = (usage.get("cache_read", 0) / T) > 210_000 if T else False
    return {"turns": T, "T_total": usage["T_total"], "cache_read": usage["cache_read"],
            "n_objects": mech["n_objects"], "obj_tokens": sum(o["size"] for o in objs),
            "mech_pct_T_total": mech["pct_of_T_total"], "mech_retired": mech["n_retired"],
            "full_pct_T_total": full["pct_of_T_total"], "full_retired": full["n_retired"],
            "peak_occupancy_relief_pct": full["peak_occupancy_relief_pct"],
            "mean_occupancy_relief_tokens": full["mean_occupancy_relief_tokens"],
            "batch_events": batch["n_events"], "batch_raw_net_pct": batch["raw_net_pct_of_T_total"],
            "batch_cost_net_pct": batch["cost_net_pct_of_baseline"],
            "multiwindow": multiwindow, "transcript": transcript_path}


_BUCKETS = ((0, 60), (60, 100), (100, 150), (150, 10 ** 9))


def _agg(rows, field):
    xs = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


def stratify(rows, buckets=_BUCKETS):
    """Group per-session rows by turn-count bucket, report mean ceilings + cost NET per stratum."""
    out = []
    for lo, hi in buckets:
        sel = [r for r in rows if lo <= r["turns"] < hi and not r["multiwindow"]]
        if not sel:
            continue
        out.append({"bucket": f"{lo}-{hi if hi < 10 ** 8 else '+'}", "n": len(sel),
                    "mean_turns": _agg(sel, "turns"),
                    "mech_pct_T_total": _agg(sel, "mech_pct_T_total"),
                    "full_pct_T_total": _agg(sel, "full_pct_T_total"),
                    "peak_occupancy_relief_pct": _agg(sel, "peak_occupancy_relief_pct"),
                    "batch_raw_net_pct": _agg(sel, "batch_raw_net_pct"),
                    "batch_cost_net_pct": _agg(sel, "batch_cost_net_pct")})
    return out


def run_b3(results_json, *, arm=None, extra_transcripts=(), include_tail=True, batch_K=10):
    """arm=None pools every session (the search reducer barely perturbs read/edit/bash residency
    structure, so all arms are valid residency samples); pass an arm string to restrict."""
    res = json.load(open(results_json))
    rows = []
    seen = set()
    for key, m in res.items():
        if (arm and f"|{arm}|" not in key) or not isinstance(m, dict) or "error" in m:
            continue
        tp = m.get("transcript")
        if tp and os.path.exists(tp) and tp not in seen:
            seen.add(tp)
            try:
                rows.append(analyze_session(tp, include_tail=include_tail, batch_K=batch_K))
            except Exception as e:      # noqa: BLE001
                rows.append({"transcript": tp, "error": str(e)})
    extras = []
    for tp in extra_transcripts:
        if tp and os.path.exists(tp):
            extras.append(analyze_session(tp, include_tail=include_tail, batch_K=batch_K))
    ok = [r for r in rows if "error" not in r]
    return {"n_sessions": len(ok), "arm": arm, "batch_K": batch_K,
            "single_window_n": sum(1 for r in ok if not r["multiwindow"]),
            "strata": stratify(ok), "extras": extras, "per_session": ok}


def _main(argv):
    extras = argv[2:] if len(argv) > 2 else []
    out = run_b3(argv[1], extra_transcripts=extras)
    print("=== B3.0 retroactive CONTEXT-RETIREMENT ceiling (offline, zero-quota) ===")
    print(f"  sessions: {out['n_sessions']} ({out['single_window_n']} single-window, used for strata)\n")
    print(f"  {'bucket':>8} {'n':>3} {'µturns':>7} {'mech%':>7} {'+tail%':>7} {'peakRelief':>10} {'rawNet%':>8} {'costNet%':>8}")
    for s in out["strata"]:
        print(f"  {s['bucket']:>8} {s['n']:>3} {s['mean_turns']:>7} {s['mech_pct_T_total']:>7} "
              f"{s['full_pct_T_total']:>7} {s['peak_occupancy_relief_pct']:>10} "
              f"{s['batch_raw_net_pct']:>8} {s['batch_cost_net_pct']:>8}")
    for e in out["extras"]:
        mw = " (MULTI-WINDOW: overstated)" if e["multiwindow"] else ""
        print(f"\n  extra: {e['turns']}t mech={e['mech_pct_T_total']}% +tail={e['full_pct_T_total']}% "
              f"rawNet={e['batch_raw_net_pct']}% costNet={e['batch_cost_net_pct']}%{mw}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
