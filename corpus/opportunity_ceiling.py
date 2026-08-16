#!/usr/bin/env python3
"""Offline opportunity-ceiling analysis over the frozen Observation Corpus v2.1 journals.

Zero LLM cost: this is pure arithmetic over already-collected, already-classified data. It answers
a question the Semantic Admission Experiment couldn't, because adoption never happened there: IF a
runtime could safely substitute/reduce reads on the agent's behalf (rather than ask it to opt in),
how much of the corpus's read-token volume is even a plausible candidate?

Reuses the FROZEN classifier (`classify.classify_reads`, canonical window=16) and the frozen event
normalizer (`normalize.to_events`) exactly as `labelreport.build_report` does -- this script adds
no new classification logic, only a bucketing of the EXISTING labels/reasons into reduction
candidacy, and sums the EXISTING `model_visible_tokens` field. Read-only: never touches a journal.

Bucket taxonomy (deliberately conservative -- mirrors the classifier's own "never manufacture
exploration" stance; an UNKNOWN read is never optimistically reclassified as reducible just to
raise the ceiling):

    required              EDIT_PRECONDITION reads -- must remain exact, never a candidate.
    verification           VERIFICATION reads -- a post-edit re-check; reported, not summed into
                           either ceiling (not exploration, and not confidently a full-file need).
    exploration_reducible  EXPLORATION reads -- confidently no future mutation; the classic
                           semantic-substitution candidate. This alone is C_safe's numerator.
    search_listing_reducible  UNKNOWN reads whose reason is specifically
                           non_file_materialization_role_unresolved (a grep/find/path-listing/
                           derived materialization, not a specific file's pre-edit state) -- a
                           DIFFERENT reduction mechanism (search-OUTPUT compaction, i.e. the
                           existing Phase-1 ContextReduce reducers), not semantic substitution.
                           Added to exploration_reducible only in C_upper, the optimistic ceiling.
    unresolved_other       Every other UNKNOWN reason (content_version_conflict, read_version_race,
                           outside_causal_window, superseded_by_later_eligible_read,
                           unverified_mutation_boundary, parallel_order_ambiguous,
                           prior_unverified_mutation) -- the classifier is correctly uncertain;
                           NOT counted as reducible in either ceiling.
    config_required         CONFIG_REQUIRED reads -- a grade-C role hint, reported separately.

Only FULLY-MEASURED reads (token_attribution=='attributed' AND a non-NULL weight AND
token_status=='text', matching labelreport._tok_cat exactly) contribute to the token denominator.
ambiguous_composite / ambiguous_multipath / multimodal reads are reported but excluded, same as
the frozen label-report's own methodology -- never silently folded into either bucket.

Usage: python3 corpus/opportunity_ceiling.py <runs_dir> <out.json>
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

from contextruntime.classify import (CONFIG_REQUIRED, EDIT_PRECONDITION, EXPLORATION, UNKNOWN,
                                     VERIFICATION, classify_reads)
from contextruntime.normalize import to_events

PRIMARY_WINDOW = 16   # matches labelreport.PRIMARY_WINDOW -- the preregistered canonical window

SEARCH_LISTING_REASON = "non_file_materialization_role_unresolved"

BUCKETS = ("required", "verification", "exploration_reducible", "search_listing_reducible",
          "unresolved_other", "config_required")


def _tok_cat(row: dict) -> str:
    """Exact port of labelreport.build_report's inner _tok_cat -- a read is fully measured only
    when attribution=='attributed' AND a non-NULL weight AND token_status=='text'."""
    attr, tok, st = row.get("token_attribution"), row.get("model_visible_tokens"), row.get("token_status")
    if attr == "ambiguous_composite":
        return "ambiguous_composite"
    if attr == "ambiguous_multipath":
        return "ambiguous_multipath"
    if attr == "attributed" and tok is not None and st == "text":
        return "fully_attributed_text"
    if attr == "attributed" and tok is not None and st == "partial_multimodal":
        return "partial_multimodal"
    if st in ("multimodal_unmeasured", "unsupported"):
        return st
    return "unmeasured_other"


def _bucket_of(label) -> str:
    if label.observed_class == EDIT_PRECONDITION:
        return "required"
    if label.observed_class == VERIFICATION:
        return "verification"
    if label.observed_class == EXPLORATION:
        return "exploration_reducible"
    if label.observed_class == CONFIG_REQUIRED:
        return "config_required"
    if label.observed_class == UNKNOWN and label.reason == SEARCH_LISTING_REASON:
        return "search_listing_reducible"
    return "unresolved_other"


def analyze_journal(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events ORDER BY seq")]
    conn.close()
    events = to_events(rows)
    row_by_id = {r["event_id"]: r for r in rows}
    labels = classify_reads(events, window=PRIMARY_WINDOW, distance_key="step")

    tokens = {b: 0 for b in BUCKETS}
    counts = {b: 0 for b in BUCKETS}
    excluded_tokens = defaultdict(int)   # tok_cat -> tokens, for non-fully-measured reads (reported, not summed)
    excluded_counts = defaultdict(int)

    for eid, label in labels.items():
        row = row_by_id.get(eid)
        if row is None:
            continue
        bucket = _bucket_of(label)
        cat = _tok_cat(row)
        if cat == "fully_attributed_text":
            tokens[bucket] += row["model_visible_tokens"] or 0
            counts[bucket] += 1
        else:
            excluded_tokens[cat] += row.get("model_visible_tokens") or 0
            excluded_counts[cat] += 1

    return {"tokens": tokens, "counts": counts,
           "excluded_tokens": dict(excluded_tokens), "excluded_counts": dict(excluded_counts)}


def aggregate(runs_dir: str) -> dict:
    per_run = []
    total_tokens = {b: 0 for b in BUCKETS}
    total_counts = {b: 0 for b in BUCKETS}
    total_excluded_tokens = defaultdict(int)
    total_excluded_counts = defaultdict(int)
    by_stratum_tokens = defaultdict(lambda: {b: 0 for b in BUCKETS})

    for run_dir in sorted(glob.glob(os.path.join(runs_dir, "run-*"))):
        journal = os.path.join(run_dir, "journal.sqlite")
        manifest_path = os.path.join(run_dir, "manifest.json")
        if not (os.path.exists(journal) and os.path.exists(manifest_path)):
            continue
        manifest = json.load(open(manifest_path))
        r = analyze_journal(journal)
        run_name = os.path.basename(run_dir)
        per_run.append({"run": run_name, "task_id": manifest.get("task_id"),
                        "stratum": manifest.get("category"), **r})
        for b in BUCKETS:
            total_tokens[b] += r["tokens"][b]
            total_counts[b] += r["counts"][b]
            by_stratum_tokens[manifest.get("category", "unknown")][b] += r["tokens"][b]
        for cat, tok in r["excluded_tokens"].items():
            total_excluded_tokens[cat] += tok
        for cat, n in r["excluded_counts"].items():
            total_excluded_counts[cat] += n

    t_all = sum(total_tokens.values())   # all FULLY-MEASURED read tokens (the honest denominator)

    def ratio(numerator: int) -> float | None:
        return round(numerator / t_all, 4) if t_all else None

    c_safe_tokens = total_tokens["exploration_reducible"]
    c_upper_tokens = total_tokens["exploration_reducible"] + total_tokens["search_listing_reducible"]

    by_stratum = {}
    for stratum, buckets in sorted(by_stratum_tokens.items()):
        s_total = sum(buckets.values())
        by_stratum[stratum] = {
            "total_fully_measured_tokens": s_total,
            "c_safe": round(buckets["exploration_reducible"] / s_total, 4) if s_total else None,
            "c_upper": round((buckets["exploration_reducible"] + buckets["search_listing_reducible"])
                             / s_total, 4) if s_total else None,
            "tokens": buckets,
        }

    return {
        "schema": "opportunity-ceiling-v1",
        "n_runs": len(per_run),
        "primary_window": PRIMARY_WINDOW,
        "total_fully_measured_tokens": t_all,
        "bucket_tokens": total_tokens,
        "bucket_event_counts": total_counts,
        "excluded_tokens": dict(total_excluded_tokens),
        "excluded_event_counts": dict(total_excluded_counts),
        "c_safe": {"definition": "exploration_reducible / all_fully_measured_read_tokens",
                  "tokens": c_safe_tokens, "ratio": ratio(c_safe_tokens)},
        "c_upper": {"definition": "(exploration_reducible + search_listing_reducible) / "
                                  "all_fully_measured_read_tokens",
                   "tokens": c_upper_tokens, "ratio": ratio(c_upper_tokens)},
        "by_stratum": by_stratum,
        "per_run": per_run,
    }


if __name__ == "__main__":
    runs_dir, out_path = sys.argv[1], sys.argv[2]
    result = aggregate(runs_dir)
    json.dump(result, open(out_path, "w"), indent=2, sort_keys=True)
    headline = {k: result[k] for k in ("n_runs", "total_fully_measured_tokens", "bucket_tokens",
                                       "c_safe", "c_upper")}
    print(json.dumps(headline, indent=2))
