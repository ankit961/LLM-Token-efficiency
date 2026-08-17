#!/usr/bin/env python3
"""Step-5 A/B/C experiment harness for the B1 transparent reducer (Step-5.2 — fidelity repaired).

Justified by Step-4 (R_direct(256,400)=12.1% ≫ 8% gate). Same task prompt across arms; only the
reducer WIRING differs, realized as an arm-specific settings file whose PostToolUse reducer command
carries that arm's env INLINE.

    A_native   native output + reducer hook in OBSERVE (equal instrumentation; NOT hook-free)
    B_shipped  enforce, graph OFF, budget 256 / floor 400   (deployed default)
    B_tuned    enforce, graph OFF, budget 64  / floor 400   (budget lever)
    C_graph    enforce, graph ON,  budget 256 / floor 400   (graph vs simple at equal budget)

TOKEN ACCOUNTING (the load-bearing correction). HookJournal stamps `model_visible_tokens` at
PostToolBatch time from the MODEL-VISIBLE response (`measure_model_visible_response`), i.e. AFTER
`updatedToolOutput` replacement — so the journal ALREADY reflects the reduction. Effective tokens
are therefore the journal total DIRECTLY; the reducer's own `saved_tokens` is a CROSS-CHECK, never a
second subtraction (that would double-count). Run one grep CANARY (`verify_token_accounting`) before
quota to confirm, on the installed client, that per-read journal tokens ≈ the reducer's reduced size.

    Δtokens(B − A) = A.effective − B.effective          (effective = journal model-visible tokens)
    Δquality(C − B) = graph effect: total tokens are an OUTCOME (graph changes the trajectory), not
                      an invariant; the signal is fewer re-searches at equal task success.

Isolation is explicit (a subprocess inherits the parent env): every arm sets CR_REDUCE_MODE
(observe/enforce) and CR_GRAPH_MODE (off/on) explicitly, and the command clears an inherited
CR_LIVE_CLIENT_VERSION (`env -u …`) so enforcement stays on the REAL live-version probe. `run_arm`
preflights the version before spending quota and offers result:// recovery via an MCP config.
"""
from __future__ import annotations

import json
import os
import shlex
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from contextruntime import doctor
from contextruntime import install as _install
from contextruntime import corpusrunner as _cr
from corpus.opportunity_ceiling import _tok_cat


class ExperimentPreflightError(RuntimeError):
    """Raised before any Claude invocation when an enforcing arm could not actually reduce."""


def preflight_or_raise() -> str:
    """Zero-cost gate: refuse to spend quota on an enforcing arm if the LIVE client version isn't
    confirmed for output replacement — otherwise B/C would fire zero reductions and waste the run.
    Converts B1's safe product-fallback into a safe experiment ABORT. Returns the live version."""
    v = doctor.live_client_version()
    if not doctor.output_replacement_confirmed(v):
        raise ExperimentPreflightError(
            f"live client version {v!r} is not confirmed for output replacement "
            f"(allowlist {sorted(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)}); an enforcing arm "
            f"would reduce nothing. Re-verify output replacement on this version before the run.")
    return v


@dataclass(frozen=True)
class ReducerArm:
    name: str
    enforce: bool
    graph: bool
    budget: Optional[int]
    floor: Optional[int]
    note: str = ""

    def env(self, *, live_cas_db: str, decision_log: str, graph_db: Optional[str] = None,
            repo_id: Optional[str] = None, journal_db: Optional[str] = None) -> dict:
        """Explicit, sanitized reducer-hook env. CR_REDUCE_MODE and CR_GRAPH_MODE are ALWAYS set
        (so an inherited enforce/graph can't leak into the wrong arm). CR_LIVE_CLIENT_VERSION is
        never set here — it is cleared at the command level so the real probe governs enforcement."""
        e = {"CR_DB": live_cas_db, "CR_DECISION_LOG": decision_log,
             "CR_REDUCE_MODE": "enforce" if self.enforce else "observe",
             "CR_GRAPH_MODE": "on" if self.graph else "off"}
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
    "A_native":  ReducerArm("A_native",  enforce=False, graph=False, budget=None, floor=None,
                            note="native output + reducer hook in observe (equal instrumentation)"),
    "B_shipped": ReducerArm("B_shipped", enforce=True,  graph=False, budget=256, floor=400),
    "B_tuned":   ReducerArm("B_tuned",   enforce=True,  graph=False, budget=64,  floor=400),
    "C_graph":   ReducerArm("C_graph",   enforce=True,  graph=True,  budget=256, floor=400),
}


# --------------------------------------------------------------------- arm settings + mcp (bridge)
def arm_reducer_cmd(arm: ReducerArm, *, live_cas_db: str, decision_log: str,
                    graph_db: Optional[str] = None, repo_id: Optional[str] = None,
                    journal_db: Optional[str] = None, python: str = sys.executable) -> str:
    """PostToolUse reducer command for this arm. `env -u CR_LIVE_CLIENT_VERSION` clears any inherited
    override so the fail-safe live probe governs; the arm's vars are set explicitly (overriding
    inherited); B carries CR_GRAPH_MODE=off so graph cannot engage even if CR_GRAPH_DB is inherited."""
    pairs = {"PYTHONPATH": _install._pkg_root(),
             **arm.env(live_cas_db=live_cas_db, decision_log=decision_log,
                       graph_db=graph_db, repo_id=repo_id, journal_db=journal_db)}
    setvars = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in pairs.items())
    argv = [python, "-m", "contextruntime.reducers.hook"]
    return "env -u CR_LIVE_CLIENT_VERSION " + setvars + " " + " ".join(shlex.quote(a) for a in argv)


def crhook_cmd(journal_db: str) -> str:
    argv = _install.cli_argv() + ["cr-hook", "--db", journal_db]
    return _install._pythonpath_prefix() + " ".join(shlex.quote(a) for a in argv)


def build_arm_settings(arm: ReducerArm, *, journal_db: str, live_cas_db: str, decision_log: str,
                       graph_db: Optional[str] = None, repo_id: Optional[str] = None,
                       base_settings: Optional[dict] = None) -> dict:
    reducer = arm_reducer_cmd(arm, live_cas_db=live_cas_db, decision_log=decision_log,
                              graph_db=graph_db, repo_id=repo_id, journal_db=journal_db)
    return _install.merge_hooks(base_settings or {}, crhook_cmd(journal_db),
                                crpolicy_cmd=None, reducer_cmd=reducer)


def build_arm_mcp_config(*, mcp_store_db: str, live_cas_db: str, repo_id: str = "repo") -> dict:
    """A ContextRuntime MCP config so the agent can resolve `result://` handles via context_expand
    (recovery must be CALLABLE, not just persisted). CR_DB points the server at the arm's live CAS —
    context_expand falls back to it for result:// (equal recovery for every arm)."""
    argv = _install.cli_argv()
    return {"mcpServers": {"contextruntime": {
        "command": argv[0],
        "args": argv[1:] + ["mcp", "--db", mcp_store_db, "--repo", repo_id],
        "env": {"CR_DB": live_cas_db, "PYTHONPATH": _install._pkg_root()}}}}


def run_arm(arm: ReducerArm, backend, *, worktree: str, spec, run_dir: str, journal_db: str,
            live_cas_db: str, decision_log: str, graph_db: Optional[str] = None,
            repo_id: str = "repo", with_recovery_mcp: bool = True):
    """Execution bridge: preflight → write arm settings (+ recovery MCP) → run the backend."""
    if arm.enforce:
        preflight_or_raise()
    os.makedirs(run_dir, exist_ok=True)
    settings = build_arm_settings(arm, journal_db=journal_db, live_cas_db=live_cas_db,
                                  decision_log=decision_log, graph_db=graph_db, repo_id=repo_id)
    settings_path = os.path.join(run_dir, f"settings-{arm.name}.json")
    json.dump(settings, open(settings_path, "w"), indent=2)

    mcp_path = None
    if with_recovery_mcp:
        mcp_store = os.path.join(run_dir, f"mcp-store-{arm.name}.db")
        from contextruntime.store import GraphStore
        GraphStore(mcp_store).close()                       # an (empty) store is enough for recovery
        cfg = build_arm_mcp_config(mcp_store_db=mcp_store, live_cas_db=live_cas_db, repo_id=repo_id)
        mcp_path = os.path.join(run_dir, f"mcp-{arm.name}.json")
        json.dump(cfg, open(mcp_path, "w"), indent=2)
    return backend.run(worktree, spec, journal_db, settings_path, mcp_config_path=mcp_path)


# --------------------------------------------------------------------- graph provenance (C)
def build_task_graph(worktree: str, base_commit: str, out_db: str, repo_id: str = "repo") -> dict:
    """Index the EXACT locked worktree into `out_db` and stamp provenance binding the graph to the
    task's base_commit — so C cannot silently rank against a stale graph from another commit."""
    from contextruntime.store import GraphStore
    from contextruntime.codegraph.builder import index_path
    store = GraphStore(out_db)
    rep = index_path(store, worktree, repo_id)
    store.commit(); store.close()
    prov = {"base_commit": base_commit, "index_root": os.path.abspath(worktree),
            "graph_db_sha256": _cr._sha_file(out_db), "repo_id": repo_id,
            "files_indexed": rep.files}
    json.dump(prov, open(out_db + ".provenance.json", "w"), indent=2)
    return prov


def verify_graph_provenance(out_db: str, base_commit: str) -> bool:
    try:
        prov = json.load(open(out_db + ".provenance.json"))
    except Exception:                       # noqa: BLE001
        return False
    return prov.get("base_commit") == base_commit and prov.get("graph_db_sha256") == _cr._sha_file(out_db)


# --------------------------------------------------------------------- complete controlled runner
class Step5Runner:
    """Owns one immutable controlled run: fresh worktree, (for C) a graph built from THAT worktree,
    the arm run, and persisted artifacts (agent.patch, agent-result.json, manifest.json with
    budget_walltime + graph provenance, hashes.json, evaluation.json). Reuses corpusrunner."""

    def __init__(self, repo_path: str, backend, evaluator=None):
        self.setup = _cr.TaskSetup(repo_path)
        self.backend = backend
        self.evaluator = evaluator or _cr.LocalStubEvaluator()

    def run_task(self, spec, arm: ReducerArm, out_dir: str, *, repo_id: str = "repo") -> str:
        if os.path.exists(out_dir) and os.listdir(out_dir):
            raise FileExistsError(f"{out_dir} is not empty — controlled runs require a clean dir")
        os.makedirs(out_dir, exist_ok=True)
        worktree = self.setup.worktree(spec.base_commit, os.path.join(out_dir, "worktree"))
        journal_db = os.path.join(out_dir, "journal.sqlite")
        live_cas_db = os.path.join(out_dir, "live_cas.db")
        decision_log = os.path.join(out_dir, "reduce_decisions.jsonl")

        graph_db, graph_prov = None, None
        if arm.graph:
            graph_db = os.path.join(out_dir, "codegraph.db")
            graph_prov = build_task_graph(worktree, spec.base_commit, graph_db, repo_id)

        res = run_arm(arm, self.backend, worktree=worktree, spec=spec, run_dir=out_dir,
                      journal_db=journal_db, live_cas_db=live_cas_db, decision_log=decision_log,
                      graph_db=graph_db, repo_id=repo_id)

        # persist immutable artifacts (mirrors corpusrunner's contract; wall-time lives in manifest)
        with open(os.path.join(out_dir, "agent.patch"), "w") as fh:
            fh.write(res.patch or "")
        json.dump(res.result, open(os.path.join(out_dir, "agent-result.json"), "w"), indent=2)
        ev = self.evaluator.evaluate(spec, res.patch or "")
        json.dump({"status": ev.status, "resolved": ev.resolved, **ev.detail},
                  open(os.path.join(out_dir, "evaluation.json"), "w"), indent=2)
        manifest = {"task_id": spec.task_id, "category": spec.category, "base_commit": spec.base_commit,
                    "arm": arm.name, "agent_version": res.agent_version, "model": res.model,
                    "budget_walltime": res.budget_walltime, "termination_reason": res.termination_reason,
                    "graph_provenance": graph_prov}
        json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2)
        hashes = {n: _cr._sha_file(os.path.join(out_dir, n)) for n in
                  ("journal.sqlite", "agent.patch", "agent-result.json", "evaluation.json",
                   "manifest.json") if os.path.exists(os.path.join(out_dir, n))}
        json.dump(hashes, open(os.path.join(out_dir, "hashes.json"), "w"), indent=2)
        self.setup.cleanup(worktree)
        return out_dir


# --------------------------------------------------------------------- task selection
def select_search_heavy_tasks(replay_json_path: str, k: int = 4) -> list:
    d = json.load(open(replay_json_path))
    ranked = sorted(d["per_run"], key=lambda r: (-r.get("search_bucket_tokens", 0), r["run"]))
    return [{"run": r["run"], "task_id": r.get("task_id"), "stratum": r.get("stratum"),
             "search_bucket_tokens": r.get("search_bucket_tokens", 0)}
            for r in ranked[:k] if r.get("search_bucket_tokens", 0) > 0]


# --------------------------------------------------------------------- per-run metrics
def _read_decisions(decision_log_path: Optional[str]) -> list:
    if not decision_log_path:
        return []
    try:
        with open(decision_log_path) as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]
    except FileNotFoundError:
        return []


def verify_token_accounting(journal_db: str, decision_log_path: str, *, tol_frac: float = 0.10) -> dict:
    """CANARY cross-check: for each ENFORCED reduction (joined by tool_use_id), does the journal's
    model_visible_tokens match the reducer's reduced_tokens? If the journal reflects the replacement
    (as intended) they agree; a large systematic gap means the journal did NOT see the replacement
    on this client and the token accounting must be revisited BEFORE trusting Δtokens."""
    conn = sqlite3.connect(journal_db)
    conn.row_factory = sqlite3.Row
    jtok = {r["tool_use_id"]: (r["model_visible_tokens"] or 0)
            for r in conn.execute("SELECT tool_use_id, model_visible_tokens FROM tool_events "
                                  "WHERE kind='read' AND tool_use_id IS NOT NULL")}
    conn.close()
    checked, agree = 0, 0
    for d in _read_decisions(decision_log_path):
        if not d.get("enforced") or not d.get("tool_use_id") or d["tool_use_id"] not in jtok:
            continue
        checked += 1
        jt, rt = jtok[d["tool_use_id"]], d.get("reduced_tokens", 0)
        if rt and abs(jt - rt) <= tol_frac * max(rt, 1):
            agree += 1
    return {"checked": checked, "agree": agree,
            "ok": checked == 0 or agree / checked >= 0.8,
            "note": "journal model_visible_tokens vs reducer reduced_tokens for enforced reads"}


def run_metrics(journal_db: str, decision_log_path: Optional[str] = None,
                evaluation_json: Optional[str] = None, manifest_json: Optional[str] = None) -> dict:
    conn = sqlite3.connect(journal_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events WHERE kind='read'")]
    conn.close()
    # PRIMARY: the journal's model-visible tokens ALREADY reflect the reduction (PostToolBatch payload).
    effective_read_tokens = sum((r["model_visible_tokens"] or 0) for r in rows
                                if _tok_cat(r) == "fully_attributed_text")
    scopes = [r["path_normalized"] for r in rows
              if r.get("representation") in ("search", "path_listing") and r.get("path_normalized")]
    repeated_scope_count = sum(c - 1 for c in Counter(scopes).values() if c > 1)

    decisions = _read_decisions(decision_log_path)
    enforced = [d for d in decisions if d.get("enforced")]
    reducer_saved_tokens = sum(d.get("saved_tokens", 0) for d in enforced)   # CROSS-CHECK only
    fps = [d["fingerprint"] for d in decisions if d.get("fingerprint")]
    exact_search_repeat_count = sum(c - 1 for c in Counter(fps).values() if c > 1)

    resolved = None
    if evaluation_json:
        try:
            resolved = json.load(open(evaluation_json)).get("resolved")
        except Exception:                       # noqa: BLE001
            resolved = None
    wall = None
    for src in (manifest_json,):                # budget_walltime is persisted in the manifest
        if src:
            try:
                wall = json.load(open(src)).get("budget_walltime")
            except Exception:                   # noqa: BLE001
                wall = None
    return {
        "effective_read_tokens": effective_read_tokens,      # PRIMARY (journal, reflects reduction)
        "reducer_saved_tokens": reducer_saved_tokens,        # cross-check, NOT subtracted
        "search_reads": len(scopes),
        "exact_search_repeat_count": exact_search_repeat_count,   # precise (same query re-run)
        "repeated_scope_count": repeated_scope_count,            # broad (same dir, maybe other query)
        "candidates_seen": len(decisions),
        "reductions_enforced": len(enforced),
        "non_beneficial": sum(1 for d in decisions if d.get("reason") == "non_beneficial"),
        "graph_ranked": sum(1 for d in enforced if d.get("graph_ranked")),
        "wall_time_s": wall,
        "task_resolved": resolved,
    }


# --------------------------------------------------------------------- experiment deltas
def compare(arm_runs: dict) -> dict:
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
            "exact_search_repeat_delta": c["exact_search_repeat_count"] - b["exact_search_repeat_count"],
            "repeated_scope_delta": c["repeated_scope_count"] - b["repeated_scope_count"],
            "task_resolved_B": b["task_resolved"], "task_resolved_C": c["task_resolved"],
            "note": "total tokens are an OUTCOME (graph changes the trajectory), not an invariant. "
                    "The graph signal is fewer re-searches at equal task success.",
        }
        # C is valid ONLY if it actually TESTED graph-ranked reduction: reductions fired AND graph
        # engaged on them. Zero reductions is NOT a valid C (it tested nothing).
        reduction_engaged = c["reductions_enforced"] > 0
        graph_engaged = c["graph_ranked"] > 0
        out["validity"]["c_reduction_engaged"] = reduction_engaged
        out["validity"]["c_graph_engaged"] = graph_engaged
        out["validity"]["c_valid"] = reduction_engaged and graph_engaged
        if not (reduction_engaged and graph_engaged):
            out["validity"]["c_warning"] = (
                "C did not test graph-ranked reduction (needs reductions_enforced>0 AND "
                "graph_ranked>0); any C−B signal is meaningless.")
    return out
