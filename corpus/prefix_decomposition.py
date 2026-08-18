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


if __name__ == "__main__":
    import sys
    _main(sys.argv)
