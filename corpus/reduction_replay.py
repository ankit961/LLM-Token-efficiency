#!/usr/bin/env python3
"""Offline B1 reduction replay over the frozen Observation Corpus v2.1 journals — step 4.

Zero LLM cost. Estimates — from token counts alone — how much of the `search_listing_reducible`
bucket (FINDINGS §4, 29.7% of fully-measured read tokens) the B1 transparent reducer would
capture. This is a METADATA-ONLY ESTIMATE under a calibrated-cap approximation, NOT a measured
replay: the journals are metadata-only (no raw payload text exists to reduce). For a genuine
measurement, `measured_reduction()` / `true_replay_search()` run the real reducer on raw payloads
(reconstructed from transcripts) — prefer those whenever the raw text is available.

The approximation: above the MIN_REDUCE floor, `reduce_search` compacts a search/listing output
to roughly a constant CAP(budget) (calibrated here from the real reducer — ~244 tok at budget 256,
flat from 800 to 100k tok input). Modeled reduced size is then a function of raw token count alone:

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

The real reduced size is content-dependent (preserved diagnostics, rollup path lengths, retained
line lengths) AND the reducer BREAKS on the first line that doesn't fit — so a single huge match can
yield only header+rollup+handle (< CAP), while long diagnostics/paths can push it > CAP. The
cap estimate's bias direction is therefore NOT GUARANTEED (T_real may be below or above CAP); treat
it as a rough calibrated estimate, not a bound.

Only representations B1 actually reduces are counted — `search` and `path_listing`
(== `gate.REDUCIBLE_REPRESENTATIONS`). The ceiling's `search_listing_reducible` bucket also folds
in `derived` (`| wc -l`-style summaries) under the same classifier reason, but B1 leaves `derived`
untouched; counting it would overestimate capture, so it is reported separately and excluded.

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
from contextruntime.reducers.gate import route, REDUCIBLE_REPRESENTATIONS
from contextruntime.reducers.hook import MIN_REDUCE_TOKENS
from contextruntime.reducers import livecas
from corpus.opportunity_ceiling import PRIMARY_WINDOW, _bucket_of, _tok_cat

DEFAULT_BUDGETS = (64, 128, 256)
DEFAULT_FLOORS = (244, 400)          # 400 = the shipped hook floor; 244 ~= CAP(256), the tuned option
SEARCH_BUCKET = "search_listing_reducible"

# B1's gate reduces ONLY these representations. The ceiling's search_listing bucket also folds in
# `derived` (a `| wc -l`-style summary) under the same classifier reason — but B1 deliberately
# leaves `derived` untouched (it is already a summary). Counting it here would overestimate B1
# capture, so the replay filters to exactly B1's eligible set (imported, never re-listed).
ELIGIBLE_REPRESENTATIONS = REDUCIBLE_REPRESENTATIONS

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

    search_sizes: list = []          # B1-ELIGIBLE: representation ∈ {search, path_listing}
    derived_excluded: list = []      # same classifier bucket but NOT B1-eligible (derived/other)
    total_fully_measured = 0
    for eid, label in labels.items():
        row = row_by_id.get(eid)
        if row is None or _tok_cat(row) != "fully_attributed_text":
            continue
        tok = row["model_visible_tokens"] or 0
        total_fully_measured += tok
        if _bucket_of(label) == SEARCH_BUCKET:
            if row.get("representation") in ELIGIBLE_REPRESENTATIONS:
                search_sizes.append(tok)
            else:
                derived_excluded.append(tok)      # e.g. a `| wc -l` derived summary — B1 leaves it
    return {"search_sizes": search_sizes, "derived_excluded_sizes": derived_excluded,
            "total_fully_measured_tokens": total_fully_measured,
            "search_bucket_tokens": sum(search_sizes),
            "derived_excluded_tokens": sum(derived_excluded)}


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
    derived_reads = sum(len(r["derived_excluded_sizes"]) for r in per_run)
    derived_tokens = sum(r["derived_excluded_tokens"] for r in per_run)

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
        "schema": "reduction-replay-v1.1",
        "method": "metadata_only_calibrated_cap_estimate",
        "method_note": "ESTIMATE, not a measured replay: reduced size is modeled as CAP(budget) "
                       "above the floor. Real reduce_search output is content-dependent (diagnostics, "
                       "rollup path lengths, line lengths) AND breaks on the first non-fitting line, "
                       "so T_real may be BELOW CAP (one huge match → header+rollup+handle only) or "
                       "ABOVE it (long diagnostics/paths) — the bias direction is NOT guaranteed. "
                       "R_direct uses the derived-EXCLUDED search_bucket_share (see field), not the "
                       "29.7% ceiling constant (which included derived). For a real number, run "
                       "measured_reduction()/true_replay_search() on raw payloads, not token counts.",
        "n_runs": len(per_run),
        "primary_window": PRIMARY_WINDOW,
        "eligible_representations": sorted(ELIGIBLE_REPRESENTATIONS),
        "graph_note": "TOKEN-NEUTRAL: simple and graph reducers both cap at CAP(budget); these "
                      "numbers hold for both arms. Graph's retention-quality benefit is measured "
                      "live in step 5, not here.",
        "total_fully_measured_read_tokens": total_all,
        "search_bucket_tokens": bucket_total,
        "search_bucket_reads": len(all_sizes),
        "search_bucket_share": round(bucket_total / total_all, 4) if total_all else None,
        "derived_excluded": {"reads": derived_reads, "tokens": derived_tokens,
                             "note": "in the ceiling's search_listing bucket but representation is "
                                     "not B1-eligible (e.g. `| wc -l` derived summaries) — B1 leaves "
                                     "these untouched, so they are excluded from the estimate."},
        "cap_calibration": {b: calibrate_cap(b) for b in budgets},
        "concentration": concentration(all_sizes),
        "grid": grid,
        "per_run": [{k: r[k] for k in ("run", "task_id", "stratum", "search_bucket_tokens")}
                    | {"search_reads": len(r["search_sizes"]),
                       "derived_excluded_reads": len(r["derived_excluded_sizes"])}
                    for r in per_run],
    }


# ------------------------------------------------------------------ true replay (needs raw text)
def measured_reduction(raw: str, tool_name: str, tool_input: dict, *, budget: int = 256):
    """TRUE replay for when the raw payload IS available (e.g. reconstructed from transcripts):
    run the REAL gate + reducer and return (reduced_tokens, eligible). Mirrors the hook's decision
    path exactly — a call the gate passes through, a payload below MIN_REDUCE, OR one whose recovery
    would not be exact (a recognized secret, or over the CAS byte cap) is left UNCHANGED
    (reduced == raw tokens), because the live hook refuses to replace unless persisted AND exact.
    Unlike the cap estimate, this is content-exact, not an approximation."""
    d = route(tool_name, tool_input or {})
    raw_tok = _tok(raw)
    if d.passthrough or raw_tok < MIN_REDUCE_TOKENS:
        return raw_tok, False
    if not livecas.recovery_is_exact(raw):        # live hook would pass through (redacted / > byte cap)
        return raw_tok, False
    red = reduce_search(raw, tool_input or {}, budget_tokens=budget,
                        representation=d.representation or "search")
    return (red.reduced_tokens if red.invariants_ok else raw_tok), red.invariants_ok


def true_replay_search(reads, *, budget: int = 256) -> dict:
    """Aggregate a true replay over reads with raw text. `reads` = iterable of
    (raw, tool_name, tool_input). Returns MEASURED R_search — the real reducer, no cap model."""
    t_raw = t_reduced = 0
    reduced_count = 0
    for raw, tool_name, tool_input in reads:
        rt = _tok(raw)
        red_tok, eligible = measured_reduction(raw, tool_name, tool_input, budget=budget)
        t_raw += rt
        t_reduced += red_tok
        reduced_count += 1 if (eligible and red_tok < rt) else 0
    return {"method": "measured_true_replay", "budget": budget, "reads": None,
            "raw_tokens": t_raw, "reduced_tokens": t_reduced, "reduced_reads": reduced_count,
            "R_search_measured": round(1 - t_reduced / t_raw, 4) if t_raw else None}


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
