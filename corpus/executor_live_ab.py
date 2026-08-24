#!/usr/bin/env python3
"""B5.2 Stage B — LIVE paired A/B of the scoped discovery executor. Spends real quota (user-approved).

A = native `claude -p` on the task.
B = identical, plus the `discover` MCP server (contextruntime.discover_mcp) and a steering system
    prompt telling the model to use one discover call instead of grep/read chains. Native tools stay
    AVAILABLE — fallback usage is itself a measurement (cf. the SemanticFS 0/11 adoption lesson).

Per task: fresh worktree at the task's base commit; same prompt (the original session's problem
statement); same model/budget caps. Measured per arm from the CLI result JSON + the session
transcript: real API calls, usage, cost, discover calls, native discovery calls, files edited.

Hard gates (from the B5.2 spec): task success non-inferior (same source files edited + completion),
API calls −10%, Σ input −8%, no pathological fallback. The offline oracle target (13.1% calls) is a
reference, not a requirement.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys

STEER = (
    "This project provides the MCP tool mcp__cr__discover: fast LOCAL code discovery in ONE call. "
    "IMPORTANT: the cr MCP server may still be connecting when you start — if mcp__cr__discover is "
    "not yet in your tools, FIRST call WaitForMcpServers (or load it via ToolSearch) before any "
    "exploration. Then, for ANY codebase exploration (finding a definition, usages, reading the "
    "relevant code, following a traceback), call mcp__cr__discover FIRST with an identifier/pattern "
    "(and/or path/traceback) instead of running chains of Grep/Glob/ls/cat/Read calls. It returns one "
    "consolidated evidence packet with the relevant source slices and related test files. Only fall "
    "back to native Grep/Read if the packet is insufficient for a specific detail.")

_READONLY = re.compile(r"^\s*(ls|find|cat|head|tail|tree|wc|grep|rg|git\s+(log|show|diff|status|blame|grep))\b")


def worktree(mirror, base, dest):
    subprocess.run(["git", "-C", mirror, "worktree", "remove", "--force", dest], capture_output=True)
    r = subprocess.run(["git", "-C", mirror, "worktree", "add", "--detach", dest, base],
                       capture_output=True, text=True)
    if not os.path.isdir(dest):
        raise RuntimeError(r.stderr[:300])
    return dest


def run_arm(task, arm, prompt, wt, *, repo, python, model="sonnet", budget=2.5, timeout=700):
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
            "--max-budget-usd", str(budget), "--dangerously-skip-permissions"]
    if arm == "B":
        mcp = {"mcpServers": {"cr": {"command": python, "args": ["-m", "contextruntime.discover_mcp"],
                                     "env": {"PYTHONPATH": repo},
                                     "alwaysLoad": True}}}   # B5.2R: eager-load — startup waits for the
                                                             # server, schema present on call 1 (docs)
        argv += ["--mcp-config", json.dumps(mcp), "--append-system-prompt", STEER]
    env = dict(os.environ)
    env.pop("ANTHROPIC_BASE_URL", None)                  # direct; no proxy in this experiment
    env.pop("CR_GATEWAY_MODE", None)
    try:
        p = subprocess.run(argv, cwd=wt, env=env, capture_output=True, text=True, timeout=timeout)
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:      # noqa: BLE001
            res = {"parse_error": (p.stdout or "")[-400:], "stderr": (p.stderr or "")[-300:]}
    except subprocess.TimeoutExpired:
        res = {"subtype": "timeout", "total_cost_usd": None}   # transcript still analyzable
    diff = subprocess.run(["git", "-C", wt, "diff", "--name-only"], capture_output=True, text=True).stdout
    res["_edited_files"] = sorted(f for f in diff.splitlines() if f.strip())
    return res


def transcript_for(wt):
    enc = re.sub(r"[/_]", "-", os.path.abspath(wt))       # Claude Code encodes BOTH / and _ as - (Step-7 lesson)
    files = glob.glob(os.path.expanduser(f"~/.claude/projects/*{enc}*/*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def analyze_transcript(tp):
    from corpus.transcript_util import merged_records
    calls = disc = nat = fallback_after = 0
    saw_discover = False
    for rec in merged_records(tp):
        if rec.get("isSidechain"):
            continue
        m = rec.get("message") or {}
        c = m.get("content")
        if rec.get("type") == "assistant" and isinstance(c, list) and m.get("usage"):
            calls += 1
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n = b.get("name", "")
                    if n.endswith("__discover"):
                        disc += 1
                        saw_discover = True
                    elif n in ("Read", "Grep", "Glob") or (
                            n == "Bash" and _READONLY.match((b.get("input") or {}).get("command", ""))):
                        nat += 1
                        if saw_discover:
                            fallback_after += 1
    return {"api_calls": calls, "discover_calls": disc, "native_discovery_calls": nat,
            "native_after_first_discover": fallback_after}


def usage_of(res):
    tot = {"inputTokens": 0, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0, "outputTokens": 0}
    for mu in (res.get("modelUsage") or {}).values():     # SUM across models (aux + main)
        for k in tot:
            tot[k] += mu.get(k, 0) or 0
    return {"input_total": tot["inputTokens"] + tot["cacheReadInputTokens"] + tot["cacheCreationInputTokens"],
            "cache_read": tot["cacheReadInputTokens"], "cache_creation": tot["cacheCreationInputTokens"],
            "output": tot["outputTokens"], "cost_usd": res.get("total_cost_usd"),
            "num_turns": res.get("num_turns"), "status": res.get("subtype")}


def main(cfg_path):
    cfg = json.load(open(cfg_path))
    out = {"tasks": {}}
    if os.path.exists(cfg["out"]):
        out = json.load(open(cfg["out"]))                 # resume: keep completed arms
    for t in cfg["tasks"]:
        out["tasks"].setdefault(t["id"], {})
        for arm in ("A", "B"):
            if out["tasks"][t["id"]].get(arm):
                continue                                  # already recorded
            wt = worktree(cfg["mirror"], t["base"], os.path.join(cfg["workdir"], f"{t['id']}-{arm}"))
            res = run_arm(t["id"], arm, t["prompt"], wt, repo=cfg["repo"], python=cfg["python"],
                          budget=cfg.get("budget", 2.5))
            tp = transcript_for(wt)
            rec = {"usage": usage_of(res), "edited_files": res.get("_edited_files"),
                   "transcript": tp, "result_tail": (res.get("result") or "")[:160]}
            if tp:
                rec["trace"] = analyze_transcript(tp)
            out["tasks"][t["id"]][arm] = rec
            json.dump(out, open(cfg["out"], "w"), indent=2)   # incremental save after every arm
            print(f"{t['id']} {arm}: {json.dumps(rec['usage'])} edited={rec['edited_files']} "
                  f"trace={rec.get('trace')}", flush=True)
    json.dump(out, open(cfg["out"], "w"), indent=2)
    print("wrote", cfg["out"])


if __name__ == "__main__":
    main(sys.argv[1])
