#!/usr/bin/env python3
"""B2.v2 retroactive-residency ceiling — the last unmeasured lever. ZERO Claude quota.

Prospective file compaction failed (B2.3) because the agent needs a file's body to make its FIRST
edit. RETROACTIVE compaction sidesteps that: keep a file FULL while it is actively touched, and
compact its residency only AFTER its LAST touch (last read or edit), when the agent has moved on and
no future edit to it can be broken. This measures that ceiling — the "abandoned tail": how much of a
session's token-turns are files sitting FULL in the prefix long after the agent last used them.

    gross_saving = Σ_file  reduce_frac · footprint(file) · (total_turns − last_touch_turn(file))

`footprint` = the file's largest single read (its residency size). This is a GROSS ceiling on the
cache-READ saved; it ignores the cache-WRITE cost of rewriting the prefix, which is exactly what makes
retroactive hard (see `net_note`). If the gross is small, the net (after cache-write) is smaller and
the mechanism is not worth building.
"""
from __future__ import annotations

import json
import os

from contextruntime.reducers.base import tokens as _tok
from corpus.edit_recall_replay import parse_reads_edits


def _session_turns(transcript_path: str) -> int:
    turn = 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" and isinstance(msg.get("content"), list) and msg.get("usage"):
            turn += 1
    return max(turn, 1)


def retroactive_file_ceiling(transcript_path: str, t_total, *, reduce_frac: float = 0.8) -> dict:
    """Gross retroactive-file residency saving as a fraction of measured T_total."""
    reads, edits = parse_reads_edits(transcript_path)
    total_turns = _session_turns(transcript_path)
    by_file = {}
    for (t, p, c) in reads:
        f = by_file.setdefault(p, {"footprint": 0, "last": 0})
        f["footprint"] = max(f["footprint"], _tok(c))
        f["last"] = max(f["last"], t)
    for (t, p, _o) in edits:
        if p in by_file:                         # an edit keeps the file "active" through turn t
            by_file[p]["last"] = max(by_file[p]["last"], t)
    gross = sum(reduce_frac * f["footprint"] * max(total_turns - f["last"], 0) for f in by_file.values())
    tt = t_total or 0
    return {"t_total": tt, "total_turns": total_turns, "files": len(by_file),
            "gross_saving": round(gross),
            "pct_of_T_total": round(gross / tt * 100, 2) if tt else None}


def retroactive_ceiling_over_runs(results_json: str, *, arm: str = "A_native", reduce_frac: float = 0.8) -> dict:
    res = json.load(open(results_json))
    per = []
    for key, m in res.items():
        if f"|{arm}|" not in key or not isinstance(m, dict) or "error" in m:
            continue
        tp, tt = m.get("transcript"), m.get("T_total")
        if tp and tt and os.path.exists(tp):
            per.append(retroactive_file_ceiling(tp, tt, reduce_frac=reduce_frac)["pct_of_T_total"])
    xs = [x for x in per if isinstance(x, (int, float))]
    return {"n": len(xs), "mean_pct_of_T_total": round(sum(xs) / len(xs), 2) if xs else None,
            "max_pct_of_T_total": round(max(xs), 2) if xs else None,
            "per_session_pct": per,
            "net_note": "GROSS cache-read ceiling; net subtracts the cache-WRITE of rewriting the "
                        "prefix at each compaction (~prefix size per event) — the hard part."}


def _main(argv) -> None:
    r = retroactive_ceiling_over_runs(argv[1])
    print("=== B2.v2 RETROACTIVE (compact-after-last-touch) file-residency ceiling ===")
    print(f"  native sessions: {r['n']}")
    print(f"  GROSS whole-session T_total saving: mean {r['mean_pct_of_T_total']}%  max {r['max_pct_of_T_total']}%")
    print(f"  per-session %: {r['per_session_pct']}")
    print(f"  {r['net_note']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
