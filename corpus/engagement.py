#!/usr/bin/env python3
"""Post-run ENGAGEMENT analyzer for the corpus (scaffold-v1, run-as-is decision).

Does NOT touch the frozen observation methodology — it is a run-quality annotation layered on
top of the immutable run artifacts, so degenerate/no-op agent runs are VISIBLE in the corpus
summary rather than silently averaged into the observation statistics.

Per run it reads manifest.json + label-report.json and derives:
  empty_patch  : the agent produced no diff (patch_sha256 == sha256(""))
  reads/edits  : classified reads / recorded edits (agent engagement with the codebase)
  walltime     : agent wall-clock seconds
  degenerate   : empty_patch AND (reads <= 1 OR walltime < 30s) -- barely-engaged non-attempt
Failures are RETAINED (protocol); this only labels them so the 50-run no-op rate is measurable.
Usage:  python3 corpus/engagement.py <runs_dir>
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import statistics
import sys

EMPTY = "sha256:" + hashlib.sha256(b"").hexdigest()


def analyze_run(run_dir: str) -> dict:
    m = json.load(open(os.path.join(run_dir, "manifest.json")))
    reads = edits = None
    lr = os.path.join(run_dir, "label-report.json")
    if os.path.exists(lr):
        p = json.load(open(lr)).get("provenance", {})
        reads, edits = p.get("reads_classified"), p.get("edits")
    empty = m.get("patch_sha256") == EMPTY
    wt = m.get("budget_walltime")
    degenerate = bool(empty and ((reads is not None and reads <= 1) or (wt is not None and wt < 30)))
    return {
        "run": f"run-{m['run_order']:02d}", "task_id": m.get("task_id"),
        "termination_reason": m.get("termination_reason"),
        "canonical_admissible": m.get("canonical_admissible"),
        "evaluation_status": m.get("evaluation_status"),
        "empty_patch": empty, "reads": reads, "edits": edits, "walltime_s": wt,
        "degenerate": degenerate,
    }


def analyze(runs_dir: str) -> dict:
    runs = [analyze_run(d) for d in sorted(glob.glob(os.path.join(runs_dir, "run-*")))
            if os.path.exists(os.path.join(d, "manifest.json"))]
    n = len(runs)
    wts = [r["walltime_s"] for r in runs if r["walltime_s"] is not None]
    rds = [r["reads"] for r in runs if r["reads"] is not None]
    agg = {
        "n_runs": n,
        "n_empty_patch": sum(r["empty_patch"] for r in runs),
        "n_degenerate": sum(r["degenerate"] for r in runs),
        "n_engaged": sum((not r["empty_patch"]) or (r["edits"] or 0) > 0 for r in runs),
        "n_canonical_admissible": sum(bool(r["canonical_admissible"]) for r in runs),
        "n_errors": sum(r["termination_reason"] not in ("completed",) for r in runs),
        "median_walltime_s": round(statistics.median(wts), 1) if wts else None,
        "median_reads": statistics.median(rds) if rds else None,
    }
    return {"schema": "corpus-engagement-v1", "scaffold": "scaffold-v1", "aggregate": agg, "runs": runs}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: python3 corpus/engagement.py <runs_dir>", file=sys.stderr)
        return 2
    runs_dir = argv[0]
    rep = analyze(runs_dir)
    out = os.path.join(runs_dir, "engagement.json")
    json.dump(rep, open(out, "w"), indent=2)
    a = rep["aggregate"]
    print(f"engagement over {a['n_runs']} runs (scaffold-v1):")
    print(f"  engaged={a['n_engaged']}  empty_patch={a['n_empty_patch']}  degenerate={a['n_degenerate']}  "
          f"errors={a['n_errors']}  canonical_admissible={a['n_canonical_admissible']}")
    print(f"  median walltime={a['median_walltime_s']}s  median reads={a['median_reads']}")
    for r in rep["runs"]:
        flag = "DEGEN" if r["degenerate"] else ("empty" if r["empty_patch"] else "     ")
        print(f"  {r['run']} {flag} {str(r['task_id']):28s} reads={r['reads']} edits={r['edits']} "
              f"wt={r['walltime_s']}s term={r['termination_reason']}")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
