#!/usr/bin/env python3
"""Aggregate per-instance official SWE-bench harness report.json files (across shards) into the
Arm-A grading summary. Reads ONLY grading artifacts (report.json, produced by the harness on public
SWE-bench_Verified data) + the local run_index.json (task_id -> fix-shape stratum) -- no journals,
prompts, or local paths ever enter this.

Per-task fields (exactly what the Semantic Admission Experiment v1 protocol §6 specifies):
    task_id, fail_to_pass_passed, pass_to_pass_passed, resolved, grader_error

Plus overall and per-stratum success rate (S_A).

Usage: python3 corpus/aggregate_grading.py <results_dir> <run_index.json> <out_summary.json>
"""
from __future__ import annotations

import glob
import json
import os
import sys


def load_reports(results_dir: str) -> dict:
    """Merge every report.json found anywhere under results_dir (one per shard's logs tree)."""
    reports = {}
    for path in glob.glob(os.path.join(results_dir, "**", "report.json"), recursive=True):
        try:
            data = json.load(open(path))
        except Exception:      # noqa: BLE001 -- a malformed report is a grader_error, not a crash
            continue
        reports.update(data)
    return reports


def _f2p_passed(verdict: dict) -> bool:
    ts = verdict.get("tests_status", {}).get("FAIL_TO_PASS", {})
    return len(ts.get("failure", [])) == 0 and len(ts.get("success", [])) > 0


def _p2p_passed(verdict: dict) -> bool:
    ts = verdict.get("tests_status", {}).get("PASS_TO_PASS", {})
    return len(ts.get("failure", [])) == 0


def aggregate(results_dir: str, index_path: str) -> dict:
    index = json.load(open(index_path))
    reports = load_reports(results_dir)

    rows = []
    for task_id, meta in index.items():
        v = reports.get(task_id)
        if v is None:
            rows.append({
                "task_id": task_id, "stratum": meta.get("stratum"),
                "resolved": None, "fail_to_pass_passed": None, "pass_to_pass_passed": None,
                "grader_error": True, "empty_patch": meta.get("empty_patch", False),
                "note": "no report.json produced for this instance",
            })
            continue
        rows.append({
            "task_id": task_id, "stratum": meta.get("stratum"),
            "resolved": bool(v.get("resolved")),
            "fail_to_pass_passed": _f2p_passed(v), "pass_to_pass_passed": _p2p_passed(v),
            "grader_error": False, "empty_patch": meta.get("empty_patch", False),
            "patch_successfully_applied": v.get("patch_successfully_applied"),
        })

    n = len(rows)
    n_resolved = sum(1 for r in rows if r["resolved"] is True)
    n_errors = sum(1 for r in rows if r["grader_error"])

    by_stratum: dict = {}
    for r in rows:
        s = r["stratum"] or "unknown"
        d = by_stratum.setdefault(s, {"n": 0, "resolved": 0, "grader_errors": 0})
        d["n"] += 1
        d["resolved"] += int(r["resolved"] is True)
        d["grader_errors"] += int(r["grader_error"])
    for d in by_stratum.values():
        d["success_rate"] = (d["resolved"] / d["n"]) if d["n"] else None

    return {
        "schema": "arm-a-grading-summary-v1",
        "n_tasks": n, "n_resolved": n_resolved, "n_grader_errors": n_errors,
        "success_rate": (n_resolved / n) if n else None,
        "by_stratum": by_stratum,
        "tasks": sorted(rows, key=lambda r: r["task_id"]),
    }


if __name__ == "__main__":
    results_dir, index_path, out_path = sys.argv[1:4]
    summary = aggregate(results_dir, index_path)
    json.dump(summary, open(out_path, "w"), indent=2, sort_keys=True)
    headline = {k: summary[k] for k in ("n_tasks", "n_resolved", "n_grader_errors", "success_rate")}
    print(json.dumps(headline, indent=2))
    print(json.dumps(summary["by_stratum"], indent=2, sort_keys=True))
