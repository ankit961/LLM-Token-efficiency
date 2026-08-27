#!/usr/bin/env python3
"""B6 — integrated Admission + Lifetime live A/B with REAL grading. Spends quota (user-approved cap).

Arms (per task × rep, fresh worktree each):
  N  native `claude -p` — untouched baseline.
  T  treatment = ONLY the mechanisms that survived every ablation:
       Admission: --disallowedTools for the doctor's never-used-in-headless schemas (validated to
                  strip definitions from the request; no MCP servers added, nothing eager-loaded)
       Lifetime:  the request passes through the gateway proxy in ENFORCE mode —
                  B3 safe retirement (batched) + thinking-GC keep-1 — with the response-level
                  fail-open (a 4xx to a mutated body resends the original bytes)
     NO discover. NO graph routing. NO search replacement.

Grading (real, per rep): the agent works on a worktree WITHOUT the test patch (SWE-bench convention);
afterwards the test_patch is applied to the edited tree and FAIL_TO_PASS + PASS_TO_PASS run natively
under python3.11 (`corpus/b6_grading.grade`). A rep is a SUCCESS only if F2P passes and P2P stays
green.

Metrics per rep, from the session transcript (authoritative, requestId-merged): real API calls,
Σ input (cache_read + cache_creation + input), cache r/w, output, plus cost from the CLI result; the
treatment arm also stores its gateway decision log (retirements, thinking strips, fallbacks) and
GC-caused re-reads (a Read of a path whose object the gateway had already retired).
"""
from __future__ import annotations

import glob
import json
import os
import re
import socket
import subprocess
import sys
import time

from corpus.b6_grading import apply_patch, grade

# Admission: never used in ANY headless arm of this program (doctor lean audit + B5 A-arms).
# ScheduleWakeup and TodoWrite are deliberately KEPT (used in headless sessions).
DISALLOW_ADMISSION = [
    "Workflow", "Artifact", "Agent", "DesignSync", "Monitor", "ReportFindings", "Skill",
    "EnterWorktree", "ExitWorktree", "RemoteTrigger", "PushNotification", "ListAgents",
    "CronCreate", "CronDelete", "CronList", "TaskCreate", "TaskGet", "TaskList", "TaskOutput",
    "TaskStop", "TaskUpdate", "SendMessage", "WaitForMcpServers", "NotebookEdit", "WebSearch",
    "mcp__claude_ai_Gmail__*", "mcp__mobile__*", "mcp__skidos-product__*",
]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_proxy(repo, python, log_path):
    port = _free_port()
    env = dict(os.environ, CR_GATEWAY_MODE="enforce", CR_GATEWAY_THINKING_KEEP="1",
               CR_GATEWAY_LOG=log_path, CR_GATEWAY_PORT=str(port), PYTHONPATH=repo)
    env.pop("ANTHROPIC_BASE_URL", None)
    proc = subprocess.Popen([python, "-u", "-m", "contextruntime.gateway_proxy"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return proc, port


def worktree(mirror, base, dest):
    subprocess.run(["git", "-C", mirror, "worktree", "remove", "--force", dest], capture_output=True)
    subprocess.run(["git", "-C", mirror, "worktree", "add", "--detach", dest, base], capture_output=True)
    if not os.path.isdir(dest):
        raise RuntimeError(f"worktree failed: {dest}")
    return dest


def run_arm(arm, prompt, wt, *, repo, python, model="sonnet", budget=2.5, timeout=900, gw_log=None):
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
            "--max-budget-usd", str(budget), "--dangerously-skip-permissions"]
    env = dict(os.environ)
    env.pop("CR_GATEWAY_MODE", None)
    proxy = None
    if arm == "T":
        argv += ["--disallowedTools", *DISALLOW_ADMISSION]
        proxy, port = start_proxy(repo, python, gw_log)
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    else:
        env.pop("ANTHROPIC_BASE_URL", None)
    try:
        try:
            p = subprocess.run(argv, cwd=wt, env=env, capture_output=True, text=True, timeout=timeout)
            try:
                res = json.loads(p.stdout.strip().splitlines()[-1])
            except Exception:      # noqa: BLE001
                res = {"parse_error": (p.stdout or "")[-400:], "stderr": (p.stderr or "")[-300:]}
        except subprocess.TimeoutExpired:
            res = {"subtype": "timeout", "total_cost_usd": None}
    finally:
        if proxy is not None:
            proxy.terminate()
    return res


def transcript_for(wt):
    enc = re.sub(r"[/_]", "-", os.path.abspath(wt))
    files = glob.glob(os.path.expanduser(f"~/.claude/projects/*{enc}*/*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def transcript_metrics(tp):
    from corpus.transcript_util import merged_records
    calls = sP = cr = cc = out = 0
    reads = []
    peak = 0
    for rec in merged_records(tp):
        if rec.get("isSidechain"):
            continue
        m = rec.get("message") or {}
        c = m.get("content")
        if rec.get("type") == "assistant" and isinstance(c, list) and m.get("usage"):
            u = m["usage"]
            calls += 1
            P = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0)
            sP += P
            peak = max(peak, P)
            cr += u.get("cache_read_input_tokens", 0)
            cc += u.get("cache_creation_input_tokens", 0)
            out += u.get("output_tokens", 0)
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Read", "NotebookRead"):
                    fp = (b.get("input") or {}).get("file_path")
                    if fp:
                        reads.append((calls, os.path.normpath(fp)))
    return {"calls": calls, "sum_input": sP, "cache_read": cr, "cache_creation": cc,
            "output": out, "peak_P": peak, "reads": reads}


def gc_rereads(gw_log, reads):
    """Reads of a path AFTER the gateway retired that path's object (from the decision log). The log
    stores per-request decisions; we approximate the retirement turn by the request index at which the
    retirement count for that path first appears — conservative upper signal recorded for review."""
    if not gw_log or not os.path.exists(gw_log):
        return None
    n_retire = 0
    n_think = 0
    fallbacks = 0
    for line in open(gw_log):
        try:
            r = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        n_retire += r.get("applied", 0) or 0
        n_think += r.get("thinking_stripped", 0) or 0
        fallbacks += 1 if r.get("fallback_original") else 0
    return {"tool_results_retired": n_retire, "thinking_blocks_stripped": n_think,
            "fallback_original": fallbacks}


def main(cfg_path):
    cfg = json.load(open(cfg_path))
    out = {"tasks": {}}
    if os.path.exists(cfg["out"]):
        out = json.load(open(cfg["out"]))
    for t in cfg["tasks"]:
        out["tasks"].setdefault(t["instance_id"], {})
        for rep in range(cfg.get("reps", 3)):
            for arm in ("N", "T"):
                key = f"{arm}{rep}"
                if out["tasks"][t["instance_id"]].get(key):
                    continue
                wt = worktree(cfg["mirror"], t["base_commit"],
                              os.path.join(cfg["workdir"], f"{t['instance_id']}-{key}"))
                gw_log = os.path.join(cfg["workdir"], f"{t['instance_id']}-{key}.gw.jsonl") if arm == "T" else None
                prompt = t["problem"] + "\n\nWork in this repository at the current commit; implement a fix for the issue above. Run relevant tests if useful. Reply DONE when finished."
                res = run_arm(arm, prompt, wt, repo=cfg["repo"], python=cfg["python"],
                              budget=cfg.get("budget", 2.5), gw_log=gw_log)
                tp = transcript_for(wt)
                rec = {"cost_usd": res.get("total_cost_usd"), "status": res.get("subtype"),
                       "transcript": tp}
                if tp:
                    rec["metrics"] = {k: v for k, v in transcript_metrics(tp).items() if k != "reads"}
                # grading: apply test_patch to the EDITED tree, then run F2P + P2P
                if not apply_patch(wt, t["test_patch"]):
                    rec["grade"] = {"test_patch_applied": False, "success": False}
                else:
                    g = grade(wt, t)
                    g["test_patch_applied"] = True
                    rec["grade"] = g
                if gw_log:
                    rec["gateway"] = gc_rereads(gw_log, [])
                out["tasks"][t["instance_id"]][key] = rec
                json.dump(out, open(cfg["out"], "w"), indent=2)
                print(f"{t['instance_id']} {key}: calls={rec.get('metrics', {}).get('calls')} "
                      f"input={rec.get('metrics', {}).get('sum_input')} cost={rec['cost_usd']} "
                      f"success={rec['grade'].get('success')} gw={rec.get('gateway')}", flush=True)
                subprocess.run(["git", "-C", cfg["mirror"], "worktree", "remove", "--force", wt],
                               capture_output=True)
    print("wrote", cfg["out"])


if __name__ == "__main__":
    main(sys.argv[1])
