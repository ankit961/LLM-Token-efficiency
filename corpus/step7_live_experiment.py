#!/usr/bin/env python3
"""Step 7 — replicated live A/D experiment. Prices the thing offline replay cannot: whether the
mechanical compression (Step 6/6.1) turns into END-TO-END agent efficiency once recovery behavior
and altered trajectories are allowed to happen.

Arms (frontier-selected, corpus/pareto_frontier.py):
  A_native      — native output + reducer hook in observe (equal instrumentation, no reduction)
  D1_b256_f125  — budget 256, floor 125: the setting that DOMINATES shipped (256,400) on both axes
  D2_b128_f125  — budget 128, floor 125: the frontier KNEE (near-2x capture, small line-recall cost)

Graph ranking is OFF in every arm (Step 6.1: no line-level benefit). Design: 3 arms x 4 django
tasks x N reps, replicated to average the whole-session trajectory variance that made the Step-5
pilot inconclusive. Reuses Step5Runner (fresh worktree per run, per-run journal + decision log +
recovery MCP) and ClaudeBackend(capture_usage=True) so each session records its token usage.

Outcome metrics (per the reviewer's list):
  T_total          input + output + cache_creation + cache_read tokens (the real billed cost)
  turns            num_turns to resolution
  native_rereads   same file Read>1x natively (journal)
  result_expansions  context_expand (result://) calls the agent actually made (transcript)
  re_searches      exact_search_repeat_count (same query re-run; from the decision log fingerprints)
  effective_read_tokens, reductions_enforced, non_beneficial, wall_time_s  (run_metrics)
  task success: DEFERRED — patches are saved per run for later SWE-bench grading (no docker here).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import time
import traceback
from collections import Counter

from contextruntime.corpusrunner import ClaudeBackend, parse_spec
from corpus.step5_experiment import ARMS, ReducerArm, Step5Runner, run_metrics

# task_id -> frozen spec (the 4 highest search-bucket django tasks from Step-4)
TASKS = {
    "django__django-10554": "corpus/specs/run-29.md",
    "django__django-11138": "corpus/specs/run-14.md",
    "django__django-12419": "corpus/specs/run-15.md",
    "django__django-14608": "corpus/specs/run-24.md",
}

ARMS7 = {
    "A_native": ARMS["A_native"],
    "D1_b256_f125": ReducerArm("D1_b256_f125", enforce=True, graph=False, budget=256, floor=125,
                               note="frontier: dominates shipped (256,400) on both axes"),
    "D2_b128_f125": ReducerArm("D2_b128_f125", enforce=True, graph=False, budget=128, floor=125,
                               note="frontier knee: ~2x capture at small line-recall cost"),
}
ARM_ORDER = ["A_native", "D1_b256_f125", "D2_b128_f125"]


# ------------------------------------------------------------------------ new outcome metrics
def t_total(usage: dict):
    """Total billed tokens = input + output + cache-creation (write) + cache-read."""
    if not usage:
        return None
    return sum((usage.get(k) or 0) for k in
               ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def native_reread_count(journal_db: str) -> int:
    """Native file re-reads = a `file` Read of a path already Read this session (journal). This is the
    kind of extra work a too-aggressive reduction could induce (agent re-opens what it lost)."""
    conn = sqlite3.connect(journal_db)
    conn.row_factory = sqlite3.Row
    paths = [r["path_normalized"] for r in
             conn.execute("SELECT path_normalized FROM tool_events "
                          "WHERE kind='read' AND representation='file' AND path_normalized IS NOT NULL")]
    conn.close()
    return sum(c - 1 for c in Counter(paths).values() if c > 1)


def count_expansions(transcript_path):
    """Number of `context_expand` (result://) recovery calls the agent actually made — the direct
    price of the inline-evidence deficit. None if the transcript can't be located."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    n = 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and "context_expand" in str(b.get("name", "")):
                    n += 1
    return n


def transcript_for_worktree(worktree: str, *, projects_dir=None):
    """The session's OWN transcript, located by the worktree path — NOT the global newest jsonl
    (which can be an unrelated Claude session, e.g. the one orchestrating this experiment). Claude
    Code names the project dir by the session cwd with '/' and '_' replaced by '-'; the transcript
    persists there even after the worktree is cleaned up. Newest jsonl in that dir (resumes append)."""
    projects_dir = projects_dir or os.path.expanduser("~/.claude/projects")
    enc = re.sub(r"[/_]", "-", os.path.abspath(worktree))
    js = glob.glob(os.path.join(projects_dir, enc, "*.jsonl"))
    return max(js, key=os.path.getmtime) if js else None


def step7_metrics(run_dir: str, transcript_path=None) -> dict:
    """run_metrics (effective tokens, re-searches, reductions, wall) + Step-7 additions
    (T_total, turns, native re-reads, result:// expansions). Patch is saved by Step5Runner."""
    journal = os.path.join(run_dir, "journal.sqlite")
    m = run_metrics(journal, os.path.join(run_dir, "reduce_decisions.jsonl"),
                    evaluation_json=os.path.join(run_dir, "evaluation.json"),
                    manifest_json=os.path.join(run_dir, "manifest.json"))
    usage = num_turns = cost = None
    try:
        res = json.load(open(os.path.join(run_dir, "agent-result.json")))
        usage, num_turns, cost = res.get("usage"), res.get("num_turns"), res.get("total_cost_usd")
    except Exception:      # noqa: BLE001
        pass
    term = None
    try:
        term = json.load(open(os.path.join(run_dir, "manifest.json"))).get("termination_reason")
    except Exception:      # noqa: BLE001
        pass
    # T_total from the result JSON is authoritative (reconciles with total_cost_usd) but is ABSENT
    # on a wall-clock timeout — record `completed` so censored (timed-out) runs are handled honestly
    # rather than silently biasing the mean toward the fast sessions.
    m.update({
        "T_total": t_total(usage), "usage": usage, "num_turns": num_turns, "total_cost_usd": cost,
        "termination_reason": term, "completed": term == "completed",
        "native_rereads": native_reread_count(journal),
        "result_expansions": count_expansions(transcript_path),
    })
    return m


# ------------------------------------------------------------------------ orchestration
def run_experiment(django: str, out: str, *, reps: int, tasks=None, arm_names=None,
                   walltime_s: int = 900, model: str = "sonnet", client_version: str = "2.1.229",
                   max_sessions=None, log=print) -> dict:
    """Replicated A/D live experiment. Outer loop = rep (so partial results cover all arms early),
    then task, then arm. Resumable: a run dir with manifest.json is re-scored, not re-run. Writes
    step7-results.json incrementally. `max_sessions` bounds a preflight dry-run."""
    tasks = tasks or TASKS
    arm_names = arm_names or ARM_ORDER
    os.makedirs(out, exist_ok=True)
    backend = ClaudeBackend(model=model, walltime_limit_s=walltime_s,
                            client_version=client_version, capture_usage=True)
    specs = {tid: parse_spec(sf) for tid, sf in tasks.items()}
    results, ran = {}, 0
    log(f"STEP7_START django={django} arms={arm_names} tasks={list(tasks)} reps={reps}")
    for rep in range(reps):
        for tid, spec in specs.items():
            for arm in arm_names:
                key = f"{tid}|{arm}|rep{rep}"
                rd = os.path.join(out, tid.replace("/", "_"), arm, f"rep{rep}")
                worktree = os.path.join(rd, "worktree")
                if os.path.exists(os.path.join(rd, "manifest.json")):        # resumable
                    results[key] = step7_metrics(rd, transcript_for_worktree(worktree))
                    log(f"SKIP  {key} (already done)")
                    continue
                if max_sessions is not None and ran >= max_sessions:
                    log(f"STOP  max_sessions={max_sessions} reached"); _dump(results, out); return results
                log(f"START {key} base={spec.base_commit[:12]}")
                t0 = time.time()
                try:
                    Step5Runner(django, backend).run_task(spec, ARMS7[arm], rd, repo_id="django")
                    tr = transcript_for_worktree(worktree)
                    m = step7_metrics(rd, tr)
                    m["transcript"] = tr
                    results[key] = m
                    ran += 1
                    log(f"DONE  {key} term={m.get('termination_reason')} T_total={m.get('T_total')} "
                        f"turns={m.get('num_turns')} eff_read={m['effective_read_tokens']} "
                        f"reductions={m['reductions_enforced']} rereads={m['native_rereads']} "
                        f"expansions={m['result_expansions']} re_search={m['exact_search_repeat_count']} "
                        f"wall={m['wall_time_s']} {round(time.time()-t0)}s")
                except Exception as e:      # noqa: BLE001 — one bad session must not kill the sweep
                    results[key] = {"error": f"{type(e).__name__}: {e}"}
                    log(f"FAIL  {key}: {type(e).__name__}: {e}")
                    log(traceback.format_exc())
                _dump(results, out)
    log("STEP7_COMPLETE")
    _dump(results, out)
    return results


def _dump(results, out):
    json.dump(results, open(os.path.join(out, "step7-results.json"), "w"), indent=2, default=str)


# ------------------------------------------------------------------------ aggregation
def aggregate(results: dict) -> dict:
    """Per-arm means (across all task×rep) for each outcome, plus per-task means, ignoring errored
    and null-metric runs. Paired reading is per task: compare arms within the same task_id."""
    METRICS = ["T_total", "num_turns", "effective_read_tokens", "native_rereads",
               "result_expansions", "exact_search_repeat_count", "reductions_enforced", "wall_time_s"]

    def _mean(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 2) if xs else None

    by_arm = {}
    for key, m in results.items():
        if not isinstance(m, dict) or "error" in m:
            continue
        arm = key.split("|")[1]
        by_arm.setdefault(arm, []).append(m)
    # T_total is measured only on COMPLETED sessions (null on timeout); report completion so a
    # censored arm (times out more) can't masquerade as cheap. means over completed for T_total/turns.
    arm_means = {arm: {
        "n": len(ms), "completed": sum(1 for m in ms if m.get("completed")),
        "timeouts": sum(1 for m in ms if m.get("termination_reason") == "budget_walltime"),
        **{k: _mean([m.get(k) for m in ms]) for k in METRICS}}
        for arm, ms in by_arm.items()}
    # per-task, per-arm (paired)
    by_task = {}
    for key, m in results.items():
        if not isinstance(m, dict) or "error" in m:
            continue
        tid, arm, _ = key.split("|")
        by_task.setdefault(tid, {}).setdefault(arm, []).append(m)
    task_arm = {tid: {arm: {"n": len(ms), **{k: _mean([m.get(k) for m in ms]) for k in METRICS}}
                      for arm, ms in arms.items()} for tid, arms in by_task.items()}
    return {"arm_means": arm_means, "per_task": task_arm,
            "errors": [k for k, m in results.items() if isinstance(m, dict) and "error" in m]}


def _main(argv) -> None:
    """python -m corpus.step7_live_experiment <django_repo> <out_dir> [reps] [max_sessions]"""
    django, out = argv[1], argv[2]
    reps = int(argv[3]) if len(argv) > 3 else 5
    max_sessions = int(argv[4]) if len(argv) > 4 else None
    res = run_experiment(django, out, reps=reps, max_sessions=max_sessions)
    print(json.dumps(aggregate(res), indent=2, default=str))


if __name__ == "__main__":
    import sys
    _main(sys.argv)
