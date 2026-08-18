#!/usr/bin/env python3
"""Budget–floor Pareto frontier for B1 search-output reduction. ZERO Claude quota.

Composes the two offline measurements into one decision surface:
  * x = token capture  R_paired(B,F)         (corpus.paired_replay, over ALL 184 observed searches)
  * y = inline line-recall(B,F)              (corpus.graph_evidence_replay, simple arm, over the
                                              future-touch-needed subset)

A setting (B,F) is on the frontier iff no other setting captures at least as many tokens AND retains
at least as many inline evidence lines, with a strict gain on one axis. The non-dominated points are
the only ones worth spending live quota on — everything else is beaten on both axes at once.

    python -m corpus.pareto_frontier '<transcript-glob>' <pilot_dir>
"""
from __future__ import annotations

import glob

from corpus.graph_evidence_replay import (line_retention_analysis, load_task_graphs,
                                          map_transcripts_to_tasks)
from corpus.paired_replay import paired_reduction, parse_transcript

BUDGETS = (64, 96, 128, 192, 256)
FLOORS = (100, 125, 160, 200, 244, 300, 400)


def mark_nondominated(rows: list, *, x: str = "R_paired", y: str = "line_recall") -> list:
    """Tag each row `non_dominated`: True iff no other row is >= on both axes with a strict gain on
    one. Pure (no I/O) so it is unit-tested directly. None on either axis is treated as 0.0."""
    def val(r, k):
        return r[k] if r[k] is not None else 0.0
    for p in rows:
        px, py = val(p, x), val(p, y)
        p["non_dominated"] = not any(
            q is not p and val(q, x) >= px and val(q, y) >= py
            and (val(q, x) > px or val(q, y) > py) for q in rows)
    return rows


def frontier(transcript_glob: str, pilot_dir: str, *, budgets=BUDGETS, floors=FLOORS) -> list:
    """One row per (B,F): token capture, inline line-recall, and whether it is Pareto-non-dominated."""
    paths = sorted(glob.glob(transcript_glob))
    search = [e for p in paths for e in parse_transcript(p) if e.kind == "search" and e.raw_output]
    tmap = map_transcripts_to_tasks(transcript_glob)
    graphs = load_task_graphs(pilot_dir, tmap.keys())          # provenance-verified (fail loud)
    rows = []
    for b in budgets:
        for f in floors:
            pr = paired_reduction(search, budget=b, floor=f)
            lr = line_retention_analysis(tmap, graphs, budget=b, floor=f)
            rows.append({"budget": b, "floor": f, "R_paired": pr["R_paired"],
                         "reductions_fired": pr["reductions_fired"],
                         "line_recall": lr["line_recall_simple"], "needed_paths": lr["needed_paths"],
                         "named_without_match_line": lr["named_without_match_line_simple"],
                         "avg_retained_lines": lr["avg_retained_lines_simple"],
                         "kept_lines": lr["kept_lines_simple"]})
    return mark_nondominated(rows)


def _main(argv) -> None:
    rows = frontier(argv[1], argv[2])
    print(f"{'B':>4} {'F':>4} {'R_paired':>9} {'lineRec':>8} {'fired':>6} {'nw/oLine':>9} "
          f"{'avgLines':>9} {'':>3}frontier")
    for p in sorted(rows, key=lambda r: (-r["R_paired"], r["floor"])):
        flag = "<== NON-DOMINATED" if p["non_dominated"] else ""
        print(f"{p['budget']:>4} {p['floor']:>4} {p['R_paired']:>9.3f} {str(p['line_recall']):>8} "
              f"{p['reductions_fired']:>6} {p['named_without_match_line']:>9} "
              f"{str(p['avg_retained_lines']):>9}  {flag}")
    nd = [p for p in rows if p["non_dominated"]]
    print(f"\nnon-dominated settings (b,f): "
          f"{[(p['budget'], p['floor']) for p in sorted(nd, key=lambda r:-r['R_paired'])]}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
