#!/usr/bin/env python3
"""B2.0 seed — decompose the re-read prefix, so B2 targets the largest reducible slice with evidence
(the analog of Step 4 for B1). ZERO Claude quota.

Two views over existing native sessions:
  * representation view  (journals): reducible tool-READ tokens by the gate's semantic type
    (file / search / path_listing) — tells us the file bucket B1 left untouched is ~3x the search
    bucket B1 handled.
  * composition view     (transcripts): accumulated tool-output tokens by producing tool
    (file_read / bash / search / edit_echo / mcp) + the fixed system+tools floor (first-turn
    cache-creation) — tells us how much of the per-turn prefix is even ours to reduce.

Neither modifies anything; both read the Step-7 artifacts. See docs/b2-prefix-reduction-scope.md.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from collections import Counter

from contextruntime.reducers.base import tokens as _tok

_TOOL_CATEGORY = {
    "Read": "file_read", "NotebookRead": "file_read",
    "Grep": "search", "Glob": "search",
    "Edit": "edit_echo", "Write": "edit_echo", "MultiEdit": "edit_echo", "NotebookEdit": "edit_echo",
    "Bash": "bash",
}


def category(tool_name) -> str:
    n = tool_name or ""
    if n in _TOOL_CATEGORY:
        return _TOOL_CATEGORY[n]
    return "mcp" if n.startswith("mcp__") else "other_tool"


def read_bucket_by_representation(journal_glob: str) -> dict:
    """Sum model-visible READ tokens by representation across native journals — the reducible-tool
    universe the gate sees. Returns {representation: {tokens, events}} + a 'TOTAL'."""
    by_tok, by_ev = Counter(), Counter()
    for j in sorted(glob.glob(journal_glob)):
        conn = sqlite3.connect(j)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT representation, model_visible_tokens FROM tool_events "
                              "WHERE kind='read'"):
            by_tok[r["representation"]] += (r["model_visible_tokens"] or 0)
            by_ev[r["representation"]] += 1
        conn.close()
    out = {rep: {"tokens": t, "events": by_ev[rep]} for rep, t in by_tok.items()}
    out["TOTAL"] = {"tokens": sum(by_tok.values()), "events": sum(by_ev.values())}
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def transcript_for_worktree(worktree: str, *, projects_dir=None):
    projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
    js = glob.glob(os.path.join(projects_dir, re.sub(r"[/_]", "-", os.path.abspath(worktree)), "*.jsonl"))
    return max(js, key=os.path.getmtime) if js else None


def accumulated_composition(transcript_path: str) -> dict:
    """Accumulated tool-output tokens by producing-tool category, assistant text, and the fixed
    system+tools floor (first cached turn's cache-creation) — the per-session prefix composition."""
    cat, uses = Counter(), {}
    asst_text, sys_floor = 0, 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        u = msg.get("usage")
        if u and not sys_floor:
            sys_floor = u.get("cache_creation_input_tokens") or 0
        if rec.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    asst_text += _tok(b.get("text", ""))
                elif isinstance(b, dict) and b.get("type") == "tool_use":
                    uses[b.get("id")] = b.get("name")
        elif rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    cat[category(uses.get(b.get("tool_use_id")))] += _tok(_text(b.get("content")))
    return {"tool_outputs_by_category": dict(cat), "assistant_text_tokens": asst_text,
            "system_tools_floor_tokens": sys_floor, "tool_outputs_total": sum(cat.values())}


_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def session_reduction_ceiling(transcript_path: str, t_total, *, reduce_frac: float = 0.8) -> dict:
    """COMPOUNDING-aware upper bound on the whole-session T_total saving from file-read reduction.

    A file read entering at turn t is cache-read on every later turn, so reducing it `reduce_frac`
    saves `reduce_frac * tokens * (total_turns - t)` cache-read tokens over the session — the effect
    B1's search reduction lacked. EDIT-SAFE: reads of files the agent ever Edits/Writes are SPARED
    (the edit needs exact context); only reads of never-edited files count as reducible. Reports the
    reducible saving as a fraction of the measured `t_total` (the go/no-go number)."""
    reads = []                 # (turn_at_read, tokens, path)
    edit_turn = {}             # file_path -> FIRST turn it is Edited/Written
    uses = {}
    turn = 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            if msg.get("usage"):
                turn += 1
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    name, inp = b.get("name"), (b.get("input") or {})
                    uses[b.get("id")] = name
                    if name in _EDIT_TOOLS and inp.get("file_path"):
                        edit_turn.setdefault(inp["file_path"], turn)
        elif rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    if category(uses.get(b.get("tool_use_id"))) == "file_read":
                        # recover the read's file_path from the paired tool_use input if present
                        reads.append((turn, _tok(_text(b.get("content"))), b.get("tool_use_id")))
    # re-scan tool_use inputs to map read tool_use_id -> file_path (edit-safety join)
    read_path = {}
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        for b in ((rec.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Read", "NotebookRead"):
                read_path[b.get("id")] = (b.get("input") or {}).get("file_path")
    total_turns = max(turn, 1)
    saving_reducible = saving_raw = saving_residency = red_ev = spared_ev = 0
    for (t, ntok, tid) in reads:
        remaining = total_turns - t
        saving_raw += reduce_frac * ntok * max(remaining, 0)     # reduce everything (unsafe upper bound)
        e = edit_turn.get(read_path.get(tid))
        if e is None:                            # reference-only file: compact the whole remaining session
            saving_reducible += reduce_frac * ntok * max(remaining, 0)
            saving_residency += reduce_frac * ntok * max(remaining, 0)
            red_ev += 1
        else:                                    # file later edited
            #   B2.0 SPARE policy: keep the whole file full for the session (window 0)
            #   RESIDENCY policy: compact until the edit MATERIALIZES exact content (window e - t)
            saving_residency += reduce_frac * ntok * max(e - t, 0)
            spared_ev += 1
    tt = t_total or 0
    return {"t_total": tt, "total_turns": total_turns, "file_read_events": len(reads),
            "reducible_events": red_ev, "spared_events": spared_ev,
            "compounded_saving_reducible": round(saving_reducible),
            "compounded_saving_raw": round(saving_raw),
            "compounded_saving_residency": round(saving_residency),
            "pct_reducible": round(saving_reducible / tt * 100, 2) if tt else None,
            "pct_raw": round(saving_raw / tt * 100, 2) if tt else None,
            "pct_residency": round(saving_residency / tt * 100, 2) if tt else None}


def reduction_ceiling_over_runs(results_json: str, *, arm: str = "A_native", reduce_frac: float = 0.8) -> dict:
    """Aggregate the compounding-aware ceiling across native runs (join each run's stored transcript
    + measured T_total). Go/no-go: is edit-safe file-read reduction a material % of T_total?"""
    res = json.load(open(results_json))
    rows = []
    for key, m in res.items():
        if f"|{arm}|" not in key or not isinstance(m, dict) or "error" in m:
            continue
        tp, tt = m.get("transcript"), m.get("T_total")
        if tp and tt and os.path.exists(tp):
            rows.append(session_reduction_ceiling(tp, tt, reduce_frac=reduce_frac))
    def _mean(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 2) if xs else None
    def _max(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(max(xs), 2) if xs else None
    return {"n": len(rows), "reduce_frac": reduce_frac,
            "mean_pct_reducible_of_T_total": _mean([r["pct_reducible"] for r in rows]),   # B2.0 spare
            "mean_pct_residency_of_T_total": _mean([r["pct_residency"] for r in rows]),   # compact-until-edit
            "max_pct_residency_of_T_total": _max([r["pct_residency"] for r in rows]),
            "mean_pct_raw_of_T_total": _mean([r["pct_raw"] for r in rows]),
            "mean_file_read_events": _mean([r["file_read_events"] for r in rows]),
            "mean_reducible_events": _mean([r["reducible_events"] for r in rows]),
            "mean_spared_events": _mean([r["spared_events"] for r in rows]),
            "per_session_pct_residency": [r["pct_residency"] for r in rows]}


def _main(argv) -> None:
    """python -m corpus.prefix_decomposition <step7_run_dir> [worktree_for_composition]"""
    run = argv[1]
    rep = read_bucket_by_representation(os.path.join(run, "*", "A_native", "*", "journal.sqlite"))
    tot = rep["TOTAL"]["tokens"] or 1
    print("=== reducible tool-READ tokens by representation (native sessions) ===")
    for r, d in sorted(rep.items(), key=lambda kv: -kv[1]["tokens"]):
        if r == "TOTAL":
            continue
        print(f"  {str(r):14} {d['tokens']:>9} tok ({d['tokens']/tot*100:5.1f}%)  events={d['events']}")
    print(f"  {'TOTAL':14} {tot:>9} tok")
    wt = argv[2] if len(argv) > 2 else os.path.join(run, "django__django-11138", "A_native", "rep0", "worktree")
    tp = transcript_for_worktree(wt)
    if tp:
        comp = accumulated_composition(tp)
        print(f"\n=== accumulated tool-output composition ({os.path.basename(tp)}) ===")
        for k, v in sorted(comp["tool_outputs_by_category"].items(), key=lambda kv: -kv[1]):
            print(f"  {k:14} {v:>8} tok")
        print(f"  assistant_text {comp['assistant_text_tokens']:>8} tok")
        print(f"  ~system+tools floor ≈ {comp['system_tools_floor_tokens']} tok (fixed, not ours)")
    rj = os.path.join(run, "step7-results.json")
    if os.path.exists(rj):
        c = reduction_ceiling_over_runs(rj)
        print(f"\n=== B2.0 GO/NO-GO: compounding-aware, edit-safe file-read reduction ceiling ===")
        print(f"  native sessions analysed: {c['n']}  (reduce_frac={c['reduce_frac']})")
        print(f"  mean file-read events/session: {c['mean_file_read_events']}  "
              f"(reducible={c['mean_reducible_events']}, spared-as-edited={c['mean_spared_events']})")
        print(f"  whole-session T_total saving:")
        print(f"    B2.0 spare (keep edited files whole)   : mean {c['mean_pct_reducible_of_T_total']}%")
        print(f"    RESIDENCY (compact-until-edit)         : mean {c['mean_pct_residency_of_T_total']}%  "
              f"max {c['max_pct_residency_of_T_total']}%")
        print(f"    raw (unsafe, reduce everything)        : mean {c['mean_pct_raw_of_T_total']}%")
        print(f"  per-session residency %: {c['per_session_pct_residency']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
