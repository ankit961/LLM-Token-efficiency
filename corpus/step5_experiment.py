#!/usr/bin/env python3
"""Step-5 A/B/C experiment harness for the B1 transparent reducer.

Justified by Step-4 (R_direct(256,400)=12.1% ≫ 8% gate). The SAME task prompt runs across arms —
only the reducer WIRING differs. An arm is realized as an arm-specific SETTINGS file whose
PostToolUse reducer hook command carries exactly that arm's env INLINE (the reducer runs as its own
process spawned by Claude Code, so its env must come from the hook command string, not the outer
`claude -p` process — this is also what makes B-vs-C graph isolation real, §arm settings).

    A_native   native output + reducer hook in OBSERVE mode (CR_REDUCE_MODE unset). NOT a hook-free
               run — it equalizes instrumentation overhead across arms; for a pure latency baseline
               add a genuinely hook-free arm separately.
    B_shipped  reducer enforce, simple (graph OFF), budget 256 / floor 400 — the deployed default
    B_tuned    reducer enforce, simple (graph OFF), budget 64 / floor 400 — the budget lever
    C_graph    reducer enforce, GRAPH ON, budget 256 / floor 400 — graph vs simple at equal budget

Two questions, two metrics:
    Δtokens(B − A)   the REDUCTION effect. The observation journal records the RAW event (the reducer
                     replaces output INDEPENDENTLY — install.py: "the journal still records the raw
                     event"), so effective model-visible tokens are derived from the decisions:
                     effective = journal_raw_read_tokens − reducer_saved_tokens. Δ uses `effective`.
    Δquality(C − B)  the GRAPH effect. Graph ranking changes WHICH evidence Claude sees, so it can
                     change the whole trajectory — total tokens are an OUTCOME, not an invariant.
                     The retention signal is fewer re-searches / expansions at equal task success.

Deterministic + offline-testable; the live `claude -p` runs (which cost quota) go through
`run_arm(...)` binding arm → settings → a backend (`corpusrunner.ClaudeBackend`). This module never
spends quota on its own, and never overrides the live version gate (no CR_LIVE_CLIENT_VERSION).
"""
from __future__ import annotations

import json
import shlex
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from contextruntime import install as _install
from corpus.opportunity_ceiling import _tok_cat


@dataclass(frozen=True)
class ReducerArm:
    name: str
    enforce: bool
    graph: bool
    budget: Optional[int]        # None → hook default (256)
    floor: Optional[int]         # None → hook default (400)
    note: str = ""

    def env(self, *, live_cas_db: str, decision_log: str, graph_db: Optional[str] = None,
            repo_id: Optional[str] = None, journal_db: Optional[str] = None) -> dict:
        """The reducer-hook env for this arm. Deliberately does NOT set CR_LIVE_CLIENT_VERSION —
        enforcement is gated on the REAL `claude --version` probe (B1.0.4 fail-safe); asserting a
        version here would let the experiment enforce on an auto-updated, unverified binary."""
        e = {"CR_DB": live_cas_db, "CR_DECISION_LOG": decision_log}
        if self.enforce:
            e["CR_REDUCE_MODE"] = "enforce"
        if self.budget is not None:
            e["CR_REDUCE_BUDGET"] = str(self.budget)
        if self.floor is not None:
            e["CR_REDUCE_FLOOR"] = str(self.floor)
        # Graph vars are included ONLY for a graph arm — omitting them from the command is what
        # actually disables graph ranking for B (the installer's default command always embeds them,
        # so B/C isolation cannot rely on the outer env alone).
        if self.graph:
            if not (graph_db and repo_id and journal_db):
                raise ValueError("C_graph arm needs graph_db, repo_id, journal_db")
            e["CR_GRAPH_DB"] = graph_db
            e["CR_REPO_ID"] = repo_id
            e["CR_JOURNAL_DB"] = journal_db
        return e


ARMS = {
    "A_native":  ReducerArm("A_native",  enforce=False, graph=False, budget=None, floor=None,
                            note="native output + reducer hook in observe (equal instrumentation)"),
    "B_shipped": ReducerArm("B_shipped", enforce=True,  graph=False, budget=256, floor=400),
    "B_tuned":   ReducerArm("B_tuned",   enforce=True,  graph=False, budget=64,  floor=400),
    "C_graph":   ReducerArm("C_graph",   enforce=True,  graph=True,  budget=256, floor=400),
}


# --------------------------------------------------------------------- arm settings (execution bridge)
def arm_reducer_cmd(arm: ReducerArm, *, live_cas_db: str, decision_log: str,
                    graph_db: Optional[str] = None, repo_id: Optional[str] = None,
                    journal_db: Optional[str] = None, python: str = sys.executable) -> str:
    """The PostToolUse reducer hook COMMAND for this arm, with its env inline (+ PYTHONPATH for
    foreign-cwd). B's command has NO CR_GRAPH_DB → graph ranking cannot engage; C's does."""
    env = {"PYTHONPATH": _install._pkg_root(),
           **arm.env(live_cas_db=live_cas_db, decision_log=decision_log,
                     graph_db=graph_db, repo_id=repo_id, journal_db=journal_db)}
    prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
    argv = [python, "-m", "contextruntime.reducers.hook"]
    return prefix + " " + " ".join(shlex.quote(a) for a in argv)


def crhook_cmd(journal_db: str) -> str:
    """The observation cr-hook command (records the journal in every arm, equally)."""
    argv = _install.cli_argv() + ["cr-hook", "--db", journal_db]
    return _install._pythonpath_prefix() + " ".join(shlex.quote(a) for a in argv)


def build_arm_settings(arm: ReducerArm, *, journal_db: str, live_cas_db: str, decision_log: str,
                       graph_db: Optional[str] = None, repo_id: Optional[str] = None,
                       base_settings: Optional[dict] = None) -> dict:
    """A complete settings.json for this arm: the 7-event observation cr-hook block (so the journal
    captures reads identically in every arm) + the arm-specific PostToolUse reducer group."""
    reducer = arm_reducer_cmd(arm, live_cas_db=live_cas_db, decision_log=decision_log,
                              graph_db=graph_db, repo_id=repo_id, journal_db=journal_db)
    return _install.merge_hooks(base_settings or {}, crhook_cmd(journal_db),
                                crpolicy_cmd=None, reducer_cmd=reducer)


def run_arm(arm: ReducerArm, backend, *, worktree: str, spec, run_dir: str, journal_db: str,
            live_cas_db: str, decision_log: str, graph_db: Optional[str] = None,
            repo_id: Optional[str] = None):
    """Execution bridge: build the arm settings, write them, and run the backend against them.
    `backend` is a corpusrunner.ClaudeBackend (arm='native' — the reducer wiring lives in the
    settings, not the semantic-admission arm). Returns the backend's AgentResult."""
    import os
    settings = build_arm_settings(arm, journal_db=journal_db, live_cas_db=live_cas_db,
                                  decision_log=decision_log, graph_db=graph_db, repo_id=repo_id)
    settings_path = os.path.join(run_dir, f"settings-{arm.name}.json")
    os.makedirs(run_dir, exist_ok=True)
    with open(settings_path, "w") as fh:
        json.dump(settings, fh, indent=2)
    return backend.run(worktree, spec, journal_db, settings_path)


# --------------------------------------------------------------------- task selection
def select_search_heavy_tasks(replay_json_path: str, k: int = 4) -> list:
    """The k tasks with the most B1-eligible search-bucket tokens (from the Step-4 artifact),
    deterministic (ties by run id). That's where the concentration — and a reducer's leverage — is."""
    d = json.load(open(replay_json_path))
    ranked = sorted(d["per_run"], key=lambda r: (-r.get("search_bucket_tokens", 0), r["run"]))
    return [{"run": r["run"], "task_id": r.get("task_id"), "stratum": r.get("stratum"),
             "search_bucket_tokens": r.get("search_bucket_tokens", 0)}
            for r in ranked[:k] if r.get("search_bucket_tokens", 0) > 0]


# --------------------------------------------------------------------- per-run metrics
def _journal_reads(journal_db: str):
    conn = sqlite3.connect(journal_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events WHERE kind='read'")]
    conn.close()
    return rows


def _walltime(run_dir: Optional[str]):
    if not run_dir:
        return None
    import os
    for name in ("agent-result.json",):
        p = os.path.join(run_dir, name)
        try:
            d = json.load(open(p))
            return d.get("budget_walltime") or d.get("walltime")
        except Exception:                       # noqa: BLE001
            continue
    return None


def run_metrics(journal_db: str, decision_log_path: Optional[str] = None,
                evaluation_json: Optional[str] = None, run_dir: Optional[str] = None) -> dict:
    """Per-arm-run metrics. Δtokens uses `effective_read_tokens` (raw journal tokens MINUS the
    reducer's own saved_tokens), because the journal records the RAW event, not the replacement."""
    rows = _journal_reads(journal_db)
    journal_raw_read_tokens = sum((r["model_visible_tokens"] or 0) for r in rows
                                  if _tok_cat(r) == "fully_attributed_text")
    scopes = [r["path_normalized"] for r in rows
              if r.get("representation") in ("search", "path_listing") and r.get("path_normalized")]
    re_search_scope_proxy = sum(c - 1 for c in Counter(scopes).values() if c > 1)  # broad, weak

    decisions = []
    if decision_log_path:
        try:
            with open(decision_log_path) as fh:
                decisions = [json.loads(ln) for ln in fh if ln.strip()]
        except FileNotFoundError:
            decisions = []
    enforced = [d for d in decisions if d.get("enforced")]
    reducer_saved_tokens = sum(d.get("saved_tokens", 0) for d in enforced)
    # precise re-search: the SAME search call (tool+pattern+scope fingerprint) seen more than once
    fps = [d["fingerprint"] for d in decisions if d.get("fingerprint")]
    re_search_fingerprint = sum(c - 1 for c in Counter(fps).values() if c > 1)

    resolved = None
    if evaluation_json:
        try:
            resolved = json.load(open(evaluation_json)).get("resolved")
        except Exception:                       # noqa: BLE001
            resolved = None

    return {
        "journal_raw_read_tokens": journal_raw_read_tokens,
        "reducer_saved_tokens": reducer_saved_tokens,
        "effective_read_tokens": journal_raw_read_tokens - reducer_saved_tokens,
        "search_reads": len(scopes),
        "re_search_fingerprint": re_search_fingerprint,       # precise (same query re-run)
        "re_search_scope_proxy": re_search_scope_proxy,       # broad (same dir, maybe different query)
        "candidates_seen": len(decisions),
        "reductions_enforced": len(enforced),
        "non_beneficial": sum(1 for d in decisions if d.get("reason") == "non_beneficial"),
        "graph_ranked": sum(1 for d in enforced if d.get("graph_ranked")),
        "wall_time_s": _walltime(run_dir),
        "task_resolved": resolved,
    }


# --------------------------------------------------------------------- experiment deltas
def compare(arm_runs: dict) -> dict:
    """arm_runs: {arm_name: run_metrics(...)}. Δtokens(B−A) = reduction effect (on EFFECTIVE tokens);
    Δquality(C−B) = graph effect. Also flags C validity (graph must have engaged) and wall-time."""
    out = {"arms": arm_runs, "validity": {}}
    a = arm_runs.get("A_native")
    for b_name in ("B_shipped", "B_tuned"):
        b = arm_runs.get(b_name)
        if a and b:
            ae, be = a["effective_read_tokens"], b["effective_read_tokens"]
            out[f"delta_tokens_{b_name}_minus_A"] = {
                "token_reduction": ae - be,                    # positive = reducer saved context
                "reduction_frac": round((ae - be) / ae, 4) if ae else None,
                "wall_time_ratio": (round(b["wall_time_s"] / a["wall_time_s"], 3)
                                    if a.get("wall_time_s") and b.get("wall_time_s") else None),
                "task_resolved_A": a["task_resolved"], "task_resolved_B": b["task_resolved"],
            }
    b, c = arm_runs.get("B_shipped"), arm_runs.get("C_graph")
    if b and c:
        out["delta_quality_C_minus_B"] = {
            "effective_token_delta": c["effective_read_tokens"] - b["effective_read_tokens"],  # OUTCOME
            "re_search_fingerprint_delta": c["re_search_fingerprint"] - b["re_search_fingerprint"],
            "re_search_scope_delta": c["re_search_scope_proxy"] - b["re_search_scope_proxy"],
            "task_resolved_B": b["task_resolved"], "task_resolved_C": c["task_resolved"],
            "note": "total tokens are an OUTCOME (graph changes the trajectory), not an invariant. "
                    "The graph signal is fewer re-searches/expansions at equal task success.",
        }
        # C is only valid if graph ranking ACTUALLY engaged — else a broken/stale graph silently
        # collapses C into B and any C−B signal is meaningless.
        out["validity"]["c_graph_engaged"] = c["graph_ranked"] > 0 or c["reductions_enforced"] == 0
        if c["reductions_enforced"] > 0 and c["graph_ranked"] == 0:
            out["validity"]["c_graph_warning"] = ("C enforced reductions but graph_ranked==0 — graph "
                                                  "never engaged; C collapsed into B. Invalid C arm.")
    return out
