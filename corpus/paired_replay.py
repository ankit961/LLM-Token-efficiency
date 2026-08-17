#!/usr/bin/env python3
"""Step 6 — paired counterfactual replay over the pilot session transcripts. ZERO Claude quota.

The Step-5 pilot showed whole-session Δtokens(B−A) is swamped by independent-trajectory variance.
This fixes that by holding the trajectory FIXED: replay the EXACT tool outputs the agent actually
received (from `~/.claude/projects/**/*.jsonl`) through the reducer counterfactually. Because the
same events are scored under every config, the comparison is paired — no stochastic-session noise.

    R_paired(B,F) = 1 − Σ_i measured_reduction(x_i; B,F) / Σ_i T(x_i)

over EVERY reducible search event x_i on the observed trajectory (not just the ones that fired
live), using the REAL raw outputs — so this supersedes Step-4's metadata-only cap estimate with a
measured number on the live output distribution. `measured_reduction` mirrors the live hook exactly
(gate, floor, non-beneficial guard, exact-recovery gate).

The transcript also yields the read/edit/write sequence, so a search's matched paths can be scored
against what the agent SUBSEQUENTLY touched (future-usefulness) — the clean, deterministic test of
the graph hypothesis that the live pilot could not run (graph never fired). See `future_paths`.
"""
from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from typing import Optional

from contextruntime.reducers.base import tokens as _tok
from contextruntime.reducers.gate import route
from contextruntime.reducers.graphrank import _suffix_match
from contextruntime.reducers.library import search_matched_paths
from corpus.reduction_replay import measured_reduction

_TOUCH_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def _text(content) -> str:
    """Raw text of a tool_result content (string or a list of text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


@dataclass
class Event:
    seq: int
    kind: str                 # "search" | "touch"
    tool: str
    tool_use_id: str
    tool_input: dict
    raw_output: str = ""      # search only
    path: Optional[str] = None  # touch only (repo-relative-ish)


def parse_transcript(path: str) -> list:
    """Ordered events: every reducible SEARCH call (with its raw output) and every file TOUCH
    (Read/Edit/Write), in trajectory order, joined tool_use ↔ tool_result by id."""
    uses, results, order = {}, {}, []
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:                       # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b["id"]] = (b.get("name"), b.get("input") or {})
                order.append(b["id"])
            elif b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if tid:
                    results[tid] = _text(b.get("content"))
    events = []
    for i, tid in enumerate(order):
        name, inp = uses[tid]
        if name in _TOUCH_TOOLS and inp.get("file_path"):
            events.append(Event(i, "touch", name, tid, inp, path=inp["file_path"]))
            continue
        d = route(name, inp)
        if d.reduce:                            # a reducible search/listing call (Grep/Glob/Bash-grep/…)
            events.append(Event(i, "search", name, tid, inp, raw_output=results.get(tid, "")))
    return events


# --------------------------------------------------------------------- paired reduction (R_paired)
def paired_reduction(search_events, *, budget: int, floor: int) -> dict:
    """R_paired over the observed search events at (budget, floor), via the real reducer path."""
    t_raw = t_reduced = 0
    fired = 0
    for ev in search_events:
        raw = ev.raw_output
        if not raw:
            continue
        rt = _tok(raw)
        red_tok, eligible = measured_reduction(raw, ev.tool, ev.tool_input, budget=budget, floor=floor)
        t_raw += rt
        t_reduced += red_tok
        fired += 1 if (eligible and red_tok < rt) else 0
    return {"search_events": len([e for e in search_events if e.raw_output]),
            "reductions_fired": fired, "raw_tokens": t_raw, "reduced_tokens": t_reduced,
            "R_paired": round(1 - t_reduced / t_raw, 4) if t_raw else None}


def grid(search_events, budgets=(64, 128, 256), floors=(125, 244, 400)) -> list:
    return [{"budget": b, "floor": f, **paired_reduction(search_events, budget=b, floor=f)}
            for b in budgets for f in floors]


# --------------------------------------------------------------------- future-usefulness (graph test)
def future_paths(events, search_ev, k: int = 8) -> set:
    """FULL file paths the agent read/edited within the next k events after a search — the objective
    'subsequently useful' set. Full paths (not basenames) so same-basename files in different dirs
    (django has dozens of models.py/tests.py) are NOT conflated; matching is done by _suffix_match,
    which also lines up an absolute worktree touch-path against the repo-relative grep-path."""
    return {e.path for e in events
            if e.kind == "touch" and 0 < e.seq - search_ev.seq <= k and e.path}


def _needed_paths(matched_paths, future: set) -> set:
    """Matched search paths that the agent SUBSEQUENTLY touched — component-aware suffix match, so
    `django/db/models/query.py` matches `/…/worktree/django/db/models/query.py` but NEVER a
    different `django/contrib/admin/models.py`."""
    return {mp for mp in matched_paths if mp and any(_suffix_match(mp, tp) for tp in future)}


def _retained_paths(reduced_text: str, needed: set) -> set:
    """Which needed paths the compact output still NAMES verbatim — a kept `path:lineno:` line or the
    per-file rollup, both of which carry the FULL path (`_match_path`). Full-path substring, so it is
    collision-safe (a different same-basename file's presence can't spuriously satisfy it)."""
    return {mp for mp in needed if mp and mp in reduced_text}


def recall_analysis(events, search_events, *, budget: int, floor: int, k: int = 8) -> dict:
    """Does reduction keep the matched paths the agent SUBSEQUENTLY touched? For each reduced search
    event, `needed` = matched files touched in the next k steps; `recall` = fraction of `needed`
    still named in the compact output (the rest are recoverable via result://, at a re-expansion
    cost). Low recall ⇒ lowering the floor risks dropping useful evidence."""
    from contextruntime.reducers.library import reduce_search
    needed_total = retained_total = miss_events = scored = 0
    for ev in search_events:
        raw = ev.raw_output
        if not raw:
            continue
        red_tok, eligible = measured_reduction(raw, ev.tool, ev.tool_input, budget=budget, floor=floor)
        if not (eligible and red_tok < _tok(raw)):
            continue                            # not reduced ⇒ nothing dropped (full output kept)
        needed = _needed_paths(search_matched_paths(raw), future_paths(events, ev, k))
        if not needed:
            continue
        d = route(ev.tool, ev.tool_input)
        reduced_text = reduce_search(raw, ev.tool_input, budget_tokens=budget,
                                     representation=d.representation or "search").reduced_text
        retained = _retained_paths(reduced_text, needed)
        scored += 1
        needed_total += len(needed)
        retained_total += len(retained)
        miss_events += 1 if retained != needed else 0
    return {"scored_reductions": scored, "needed_paths": needed_total,
            "retained_paths": retained_total,
            "path_recall": round(retained_total / needed_total, 4) if needed_total else None,
            "reductions_with_a_miss": miss_events,
            "note": "a 'miss' is recoverable via result:// (re-expansion), not lost"}


def collect(transcript_glob: str) -> dict:
    """Parse all matching transcripts; return {transcript: events} for the pilot set."""
    return {p: parse_transcript(p) for p in sorted(glob.glob(transcript_glob))}


def pooled_recall(per_transcript, *, budget: int, floor: int, k: int = 8) -> dict:
    """Recall pooled ACROSS transcripts — scored per file (seq is per-transcript) then summed."""
    scored = needed = retained = miss = 0
    for evs in per_transcript.values():
        se = [e for e in evs if e.kind == "search" and e.raw_output]
        a = recall_analysis(evs, se, budget=budget, floor=floor, k=k)
        scored += a["scored_reductions"]; needed += a["needed_paths"]
        retained += a["retained_paths"]; miss += a["reductions_with_a_miss"]
    return {"budget": budget, "floor": floor, "scored_reductions": scored, "needed_paths": needed,
            "retained_paths": retained, "reductions_with_a_miss": miss,
            "path_recall": round(retained / needed, 4) if needed else None}


def _main(argv) -> None:
    """Reproduce docs/step6-paired-replay-findings.md tables:
        python -m corpus.paired_replay '<glob-of-session-transcripts>'"""
    import statistics as st
    per = collect(argv[1])
    search = [e for t in per.values() for e in t if e.kind == "search" and e.raw_output]
    sizes = sorted(_tok(e.raw_output) for e in search)
    print(f"transcripts={len(per)}  search_events={len(search)}  raw_tokens={sum(sizes)}")
    print(f"sizes p50/max={st.median(sizes):.0f}/{max(sizes)}  "
          f">=400={sum(s >= 400 for s in sizes)}  >=125={sum(s >= 125 for s in sizes)}")
    print("R_paired grid:")
    for row in grid(search):
        print(f"  b={row['budget']:>3} f={row['floor']:>3}  fired={row['reductions_fired']:>3}"
              f"  R_paired={row['R_paired']}")
    print("pooled recall:")
    for b, f in [(256, 400), (64, 244), (64, 125)]:
        r = pooled_recall(per, budget=b, floor=f)
        print(f"  b={b:>3} f={f:>3}  scored={r['scored_reductions']:>2}  needed={r['needed_paths']:>2}"
              f"  retained={r['retained_paths']:>2}  recall={r['path_recall']}"
              f"  misses={r['reductions_with_a_miss']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
