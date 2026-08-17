#!/usr/bin/env python3
"""Step-5 A/B/C experiment harness for the B1 transparent reducer.

Justified by Step-4 (R_direct(256,400)=12.1% ≫ 8% gate). The SAME task prompt runs across arms —
only the reducer WIRING differs (per the protocol's "same task prompt" rule):

    A_native   no reduction (observation journal only)                — baseline
    B_shipped  transparent reducer, enforce, simple, budget 256/floor 400 (the deployed default)
    B_tuned    transparent reducer, enforce, simple, budget 64 /floor 400 (the budget-only lever)
    C_graph    transparent reducer, enforce, GRAPH-ranked, budget 256/floor 400

The two questions the arms answer, and the metric for each:

    Δtokens(B − A)   the REDUCTION effect — model-visible read tokens the agent actually carried
                     (the journal records the REDUCED text, so this is a direct measurement).
    Δquality(C − B)  the GRAPH effect — NOT tokens (C and B share a budget, so their token totals
                     are ~equal by construction). It is retention QUALITY: did graph keep the
                     matches the agent went on to need, so it re-searched / re-expanded / retried
                     less? Measured as re-search count + task success parity (+ expansion/CED once
                     the MCP recovery path is instrumented live via semantic_reads).

Everything here is DETERMINISTIC and offline-testable: arm→env, search-heavy task selection from
the Step-4 artifact, and metric extraction from produced journals + decision logs are pure. The
live `claude -p` runs (which cost quota) reuse `corpusrunner.ClaudeBackend` with each arm's
settings; this module never spends quota on its own.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from corpus.opportunity_ceiling import _tok_cat

CONFIRMED_VERSION = "2.1.229"     # the version enforcement is gated on (doctor allowlist)


@dataclass(frozen=True)
class ReducerArm:
    """One arm = a reducer-hook env. `A_native` leaves CR_REDUCE_MODE unset (observe: the decision
    log still records candidates, so admission rate is measurable, but nothing is replaced)."""
    name: str
    enforce: bool
    budget: Optional[int]        # None → hook default (256)
    floor: Optional[int]         # None → hook default (400)
    graph: bool

    def env(self, *, live_cas_db: str, decision_log: str, graph_db: Optional[str] = None,
            repo_id: Optional[str] = None, journal_db: Optional[str] = None,
            client_version: str = CONFIRMED_VERSION) -> dict:
        e = {"CR_DB": live_cas_db, "CR_DECISION_LOG": decision_log,
             "CR_LIVE_CLIENT_VERSION": client_version}
        if self.enforce:
            e["CR_REDUCE_MODE"] = "enforce"
        if self.budget is not None:
            e["CR_REDUCE_BUDGET"] = str(self.budget)
        if self.floor is not None:
            e["CR_REDUCE_FLOOR"] = str(self.floor)
        if self.graph:
            if not (graph_db and repo_id and journal_db):
                raise ValueError("C_graph arm needs graph_db, repo_id, journal_db")
            e["CR_GRAPH_DB"] = graph_db
            e["CR_REPO_ID"] = repo_id
            e["CR_JOURNAL_DB"] = journal_db
        return e


ARMS = {
    "A_native":  ReducerArm("A_native",  enforce=False, budget=None, floor=None, graph=False),
    "B_shipped": ReducerArm("B_shipped", enforce=True,  budget=256,  floor=400,  graph=False),
    "B_tuned":   ReducerArm("B_tuned",   enforce=True,  budget=64,   floor=400,  graph=False),
    "C_graph":   ReducerArm("C_graph",   enforce=True,  budget=256,  floor=400,  graph=True),
}


def select_search_heavy_tasks(replay_json_path: str, k: int = 4) -> list:
    """The k tasks with the most B1-eligible search-bucket tokens (from the Step-4 artifact) —
    deterministic (ties broken by run id). These are where a reducer arm has the most to move."""
    d = json.load(open(replay_json_path))
    ranked = sorted(d["per_run"], key=lambda r: (-r.get("search_bucket_tokens", 0), r["run"]))
    return [{"run": r["run"], "task_id": r.get("task_id"),
             "stratum": r.get("stratum"), "search_bucket_tokens": r.get("search_bucket_tokens", 0)}
            for r in ranked[:k] if r.get("search_bucket_tokens", 0) > 0]


def _journal_read_tokens(journal_db: str) -> tuple:
    """(total fully-measured read tokens the model saw, list of search/path_listing scope paths).
    The journal records the MODEL-VISIBLE text, so under an enforcing arm this already reflects the
    reduction — the direct Δtokens measurement."""
    conn = sqlite3.connect(journal_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events WHERE kind='read'")]
    conn.close()
    total = sum((r["model_visible_tokens"] or 0) for r in rows
                if _tok_cat(r) == "fully_attributed_text")
    scopes = [r["path_normalized"] for r in rows
              if r.get("representation") in ("search", "path_listing") and r.get("path_normalized")]
    return total, scopes


def run_metrics(journal_db: str, decision_log_path: Optional[str] = None,
                evaluation_json: Optional[str] = None) -> dict:
    """Per-arm-run metrics from the produced artifacts. Pure over files; no agent invocation."""
    total_read_tokens, scopes = _journal_read_tokens(journal_db)
    # re-search proxy: a search/listing SCOPE materialized more than once in the session — a signal
    # the agent had to go back (e.g. because a reduction dropped something it later needed).
    scope_counts = Counter(scopes)
    re_searches = sum(c - 1 for c in scope_counts.values() if c > 1)

    decisions = []
    if decision_log_path:
        try:
            with open(decision_log_path) as fh:
                decisions = [json.loads(ln) for ln in fh if ln.strip()]
        except FileNotFoundError:
            decisions = []
    enforced = [d for d in decisions if d.get("enforced")]
    non_beneficial = [d for d in decisions if d.get("reason") == "non_beneficial"]

    resolved = None
    if evaluation_json:
        try:
            resolved = json.load(open(evaluation_json)).get("resolved")
        except Exception:                       # noqa: BLE001
            resolved = None

    return {
        "total_read_tokens": total_read_tokens,
        "search_reads": len(scopes),
        "re_searches": re_searches,
        "candidates_seen": len(decisions),
        "reductions_enforced": len(enforced),
        "non_beneficial": len(non_beneficial),
        "saved_tokens": sum(d.get("saved_tokens", 0) for d in enforced),
        "graph_ranked": sum(1 for d in enforced if d.get("graph_ranked")),
        "task_resolved": resolved,
    }


def compare(arm_runs: dict) -> dict:
    """arm_runs: {arm_name: run_metrics(...)}. Computes the two experiment deltas.
    Δtokens(B − A): reduction effect (positive `token_reduction` = the reducer saved context).
    Δquality(C − B): graph effect on retention quality (re-search delta, task-success parity)."""
    out = {"arms": arm_runs}
    a = arm_runs.get("A_native")
    for b_name in ("B_shipped", "B_tuned"):
        b = arm_runs.get(b_name)
        if a and b:
            out[f"delta_tokens_{b_name}_minus_A"] = {
                "raw_delta": b["total_read_tokens"] - a["total_read_tokens"],
                "token_reduction": a["total_read_tokens"] - b["total_read_tokens"],
                "reduction_frac": round((a["total_read_tokens"] - b["total_read_tokens"])
                                        / a["total_read_tokens"], 4) if a["total_read_tokens"] else None,
            }
    b, c = arm_runs.get("B_shipped"), arm_runs.get("C_graph")
    if b and c:
        out["delta_quality_C_minus_B"] = {
            # token-neutral by design — surface it, and expect ≈ 0
            "token_delta": c["total_read_tokens"] - b["total_read_tokens"],
            "re_search_delta": c["re_searches"] - b["re_searches"],     # negative = graph helped
            "task_resolved_B": b["task_resolved"], "task_resolved_C": c["task_resolved"],
            "note": "graph is token-neutral; its value is fewer re-searches / expansions at equal "
                    "task success. Expansion/CED plugs in from semantic_reads once wired live.",
        }
    return out
