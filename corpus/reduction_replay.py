#!/usr/bin/env python3
"""Offline B1 reduction replay over the frozen Observation Corpus v2.1 journals — step 4.

Zero LLM cost. Answers the question the opportunity-ceiling analysis left open: of the
`search_listing_reducible` bucket (FINDINGS §4 — 29.7% of fully-measured read tokens), how
much does the B1 transparent reducer ACTUALLY capture? The ceiling is candidate MASS; this is
realized SAVINGS under the reducer's real behavior.

No raw payload text is needed — and none exists in the metadata-only journals. Instead this
uses the reducer's *measured output contract*: above the MIN_REDUCE floor, `reduce_search`
compacts any search/listing output to a near-constant CAP(budget) (calibrated here from the
real reducer, not assumed — e.g. ~244 tok at budget 256, flat from 800 to 100k tok input).
So the reduced size of a read is a function of its raw token count alone:

    saved_i = raw_i - CAP(budget)   for raw_i >= threshold ;   0 otherwise
    threshold = max(floor, CAP(budget))     # you only save on reads LARGER than the cap

That makes CAP — i.e. the budget — the real lever: a smaller budget lowers the cap, so more
reads clear it. The floor only blocks reads below it; set floor <= CAP and it stops mattering.

    R_search  = sum(saved) / sum(raw over the search_listing bucket)
    R_direct  = sum(saved) / all_fully_measured_read_tokens   ( = R_search * bucket_share )

**Graph ranking (B1.2) is token-neutral**: simple and graph both cap at CAP(budget), so this
harness reports ONE token number for both arms. Graph's value — keeping the RELEVANT matches
within that cap — is a retention-QUALITY question needing raw text + a ground truth, measured
in the live step-5 A/B/C, not here. That separation is deliberate, not an omission.

Reuses the FROZEN classifier + bucketing from `opportunity_ceiling` verbatim (single source of
truth for what counts as a search/listing read); adds only arithmetic on the per-read tokens it
already isolates. Read-only: never mutates a journal.

Usage: python -m corpus.reduction_replay <runs_dir> <out.json> [--budgets 64,128,256] [--floors 244,400]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics
from collections import defaultdict

from contextruntime.normalize import to_events
from contextruntime.classify import classify_reads
from contextruntime.reducers.library import reduce_search
from contextruntime.reducers.base import tokens as _tok
from corpus.opportunity_ceiling import PRIMARY_WINDOW, _bucket_of, _tok_cat

DEFAULT_BUDGETS = (64, 128, 256)
DEFAULT_FLOORS = (244, 400)          # 400 = the shipped hook floor; 244 ~= CAP(256), the tuned option
SEARCH_BUCKET = "search_listing_reducible"

_CAP_CACHE: dict = {}


def calibrate_cap(budget: int) -> int:
    """The reducer's output size on an arbitrarily large search output at `budget` — measured
    from the real `reduce_search`, memoized. This IS the reduced-token count for any read above
    the cap, because the reducer saturates (verified flat across 800..100k-tok inputs)."""
    if budget not in _CAP_CACHE:
        line = "src/pkg/module.py:{i}: def some_function_{i}(arg): return helper(arg)"
        big = "\n".join(line.format(i=i) for i in range(4000))     # ~60k tokens, well past any cap
        _CAP_CACHE[budget] = reduce_search(big, {}, budget_tokens=budget).reduced_tokens
    return _CAP_CACHE[budget]


def scan_journal(db_path: str) -> dict:
    """Per-read scan of one run's journal: the token sizes of every fully-measured search/listing
    read, plus the run's total fully-measured read tokens (the R_direct denominator). Classification
    is the frozen `opportunity_ceiling` logic, reused unchanged."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events ORDER BY seq")]
    conn.close()
    labels = classify_reads(to_events(rows), window=PRIMARY_WINDOW, distance_key="step")
    row_by_id = {r["event_id"]: r for r in rows}

    search_sizes: list = []
    total_fully_measured = 0
    for eid, label in labels.items():
        row = row_by_id.get(eid)
        if row is None or _tok_cat(row) != "fully_attributed_text":
            continue
        tok = row["model_visible_tokens"] or 0
        total_fully_measured += tok
        if _bucket_of(label) == SEARCH_BUCKET:
            search_sizes.append(tok)
    return {"search_sizes": search_sizes, "total_fully_measured_tokens": total_fully_measured,
            "search_bucket_tokens": sum(search_sizes)}


def saved_tokens(sizes, budget: int, floor: int) -> int:
    """Total tokens the reducer would save on these reads at (budget, floor). You only save on a
    read LARGER than the cap, and only if it also clears the floor — so the effective threshold is
    max(floor, cap). Below it: passed through, zero savings (never negative)."""
    cap = calibrate_cap(budget)
    threshold = max(floor, cap)
    return sum(s - cap for s in sizes if s >= threshold)


def concentration(sizes, ref_floor: int = 400) -> dict:
    """Where the search-bucket token MASS lives — the single most decisive number, because a low
    mean with a heavy tail still yields real savings, while a uniformly-small bucket yields ~none."""
    n = len(sizes)
    total = sum(sizes)
    if not n:
        return {"reads": 0, "tokens": 0}
    srt = sorted(sizes)
    mass_above = sum(s for s in sizes if s >= ref_floor)
    edges = [(0, 120), (120, 244), (244, 400), (400, 800), (800, 2000), (2000, 10 ** 12)]
    hist = []
    for lo, hi in edges:
        rs = [s for s in sizes if lo <= s < hi]
        hist.append({"range": f"{lo}-{'inf' if hi > 10 ** 11 else hi}",
                     "reads": len(rs), "tokens": sum(rs)})
    return {
        "reads": n, "tokens": total, "mean": round(total / n, 1),
        "median": statistics.median(srt),
        "p90": srt[min(n - 1, int(0.90 * n))], "max": srt[-1],
        "mass_share_above_ref_floor": round(mass_above / total, 4) if total else 0.0,
        "ref_floor": ref_floor, "histogram": hist,
    }


def aggregate(runs_dir: str, budgets=DEFAULT_BUDGETS, floors=DEFAULT_FLOORS) -> dict:
    per_run = []
    for run_dir in sorted(glob.glob(os.path.join(runs_dir, "run-*"))):
        journal = os.path.join(run_dir, "journal.sqlite")
        manifest_path = os.path.join(run_dir, "manifest.json")
        if not (os.path.exists(journal) and os.path.exists(manifest_path)):
            continue
        manifest = json.load(open(manifest_path))
        scan = scan_journal(journal)
        per_run.append({"run": os.path.basename(run_dir), "task_id": manifest.get("task_id"),
                        "stratum": manifest.get("category"), **scan})

    all_sizes = [s for r in per_run for s in r["search_sizes"]]
    total_all = sum(r["total_fully_measured_tokens"] for r in per_run)
    bucket_total = sum(all_sizes)

    grid = []
    for budget in budgets:
        cap = calibrate_cap(budget)
        for floor in floors:
            saved = saved_tokens(all_sizes, budget, floor)
            # macro: mean of per-run R_search over runs that HAVE a search bucket
            per_run_rs = [saved_tokens(r["search_sizes"], budget, floor) / r["search_bucket_tokens"]
                          for r in per_run if r["search_bucket_tokens"] > 0]
            grid.append({
                "budget": budget, "floor": floor, "cap": cap,
                "effective_threshold": max(floor, cap),
                "saved_tokens": saved,
                "reads_reduced": sum(1 for s in all_sizes if s >= max(floor, cap)),
                "R_search_micro": round(saved / bucket_total, 4) if bucket_total else None,
                "R_direct_micro": round(saved / total_all, 4) if total_all else None,
                "R_search_macro": round(statistics.fmean(per_run_rs), 4) if per_run_rs else None,
            })

    return {
        "schema": "reduction-replay-v1",
        "n_runs": len(per_run),
        "primary_window": PRIMARY_WINDOW,
        "graph_note": "TOKEN-NEUTRAL: simple and graph reducers both cap at CAP(budget); these "
                      "numbers hold for both arms. Graph's retention-quality benefit is measured "
                      "live in step 5, not here.",
        "total_fully_measured_read_tokens": total_all,
        "search_bucket_tokens": bucket_total,
        "search_bucket_reads": len(all_sizes),
        "search_bucket_share": round(bucket_total / total_all, 4) if total_all else None,
        "cap_calibration": {b: calibrate_cap(b) for b in budgets},
        "concentration": concentration(all_sizes),
        "grid": grid,
        "per_run": [{k: r[k] for k in ("run", "task_id", "stratum",
                                       "search_bucket_tokens")} | {"search_reads": len(r["search_sizes"])}
                    for r in per_run],
    }


def _headline(result: dict) -> dict:
    ref = next((g for g in result["grid"] if g["budget"] == 256 and g["floor"] == 400), None)
    tuned = max(result["grid"], key=lambda g: g["R_direct_micro"] or 0) if result["grid"] else None
    return {"n_runs": result["n_runs"], "search_bucket_share": result["search_bucket_share"],
            "concentration_mass_above_400": result["concentration"].get("mass_share_above_ref_floor"),
            "shipped_config_256_400": ref, "best_config": tuned}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Offline B1 reduction replay (step 4).")
    ap.add_argument("runs_dir", help="dir of run-*/{journal.sqlite,manifest.json} (frozen corpus)")
    ap.add_argument("out", help="output JSON path")
    ap.add_argument("--budgets", default=",".join(map(str, DEFAULT_BUDGETS)))
    ap.add_argument("--floors", default=",".join(map(str, DEFAULT_FLOORS)))
    a = ap.parse_args(argv)
    budgets = tuple(int(x) for x in a.budgets.split(","))
    floors = tuple(int(x) for x in a.floors.split(","))
    result = aggregate(a.runs_dir, budgets, floors)
    json.dump(result, open(a.out, "w"), indent=2, sort_keys=True)
    print(json.dumps(_headline(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
