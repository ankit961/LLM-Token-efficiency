#!/usr/bin/env python3
"""B8 — live validation of B7 cache-aligned retirement in the regime where it fires.

B7's ~60% interactive dollar saving is an offline counterfactual; B6-style short headless
sessions cannot test it because the gated scheduler (correctly) never fires there. B8
manufactures the long/interactive shape under experimental control:

  ONE conversation, THREE sequential graded django tasks (each in its own worktree under a shared
  parent cwd), with a REAL idle gap > the 1h cache TTL between tasks. Context accumulates across
  the whole session; the gaps create genuine TTL-expiry windows.

Arms per pair (same script, same tasks, same gaps):
  N  native chained `claude -p` / `claude -p --resume`
  T  identical, through ONE gateway-proxy process for the whole session:
     CR_GATEWAY_MODE=enforce, thinking keep-1, CR_GATEWAY_CACHE_ALIGN=gated —
     persistent fired set + fire only on cold-start / ttl-gap / break-even

Primary endpoint (preregistered): list-price BITE ratio T/N from transcript usage
(read*0.1 + creation*2.0 + uncached*1.0 + output*5.0 — the B7 accounting, now observed live).
Secondary: CLI cost, Sigma P residency, gateway fires by reason, fallback_original, per-task
F2P+P2P grading with the B6 non-inferiority convention.

`predict` mode (zero quota) chains real B6 native timelines through the calibrated B7 model to
preregister the expected effect for THIS exact session shape before any spend.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from corpus.b6_grading import apply_patch, grade, reset_test_files
from corpus.b6_live_ab import start_proxy, transcript_metrics, worktree

GAP_S_DEFAULT = 3900          # 65 min: strictly beyond the 1h cache TTL


def bite_from_transcript(tp):
    from contextruntime.cachemodel import extract_calls, bite
    calls = extract_calls(tp)
    return {
        "calls": len(calls),
        "bite": sum(bite(c.read, c.creation, c.input, c.out) for c in calls),
        "read": sum(c.read for c in calls), "creation": sum(c.creation for c in calls),
        "output": sum(c.out for c in calls), "sum_P": sum(c.P for c in calls),
    }


def gw_fires(gw_log):
    out = {"fires": 0, "by_reason": {}, "persistent_applied": 0, "retired": 0,
           "thinking_stripped": 0, "fallback_original": 0}
    if not gw_log or not os.path.exists(gw_log):
        return None
    for line in open(gw_log):
        try:
            r = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        if r.get("fired"):
            out["fires"] += 1
            reason = r.get("fire_reason", "?")
            out["by_reason"][reason] = out["by_reason"].get(reason, 0) + 1
        out["retired"] += r.get("applied", 0) or 0
        out["persistent_applied"] = max(out["persistent_applied"], r.get("persistent_applied", 0) or 0)
        out["thinking_stripped"] += r.get("thinking_stripped", 0) or 0
        out["fallback_original"] += 1 if r.get("fallback_original") else 0
    return out


def chunk_prompt(task, wt):
    return (f"Work ONLY inside the repository at {wt} (an independent django checkout). "
            f"Implement a fix for the issue below. Run relevant tests if useful. "
            f"Reply DONE when finished.\n\n{task['problem']}")


def run_chunk(prompt, cwd, env, *, resume=None, model="sonnet", budget=2.5, timeout=1800):
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
            "--max-budget-usd", str(budget), "--dangerously-skip-permissions"]
    if resume:
        argv[1:1] = ["--resume", resume]
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:      # noqa: BLE001
            return {"parse_error": (p.stdout or "")[-400:], "stderr": (p.stderr or "")[-300:]}
    except subprocess.TimeoutExpired:
        return {"subtype": "timeout"}


def run_session(arm, tasks, session_dir, cfg, rec):
    """One 3-task chained session; incremental writes into rec (mutated in place)."""
    os.makedirs(session_dir, exist_ok=True)
    env = dict(os.environ)
    env.pop("CR_GATEWAY_MODE", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    proxy = None
    gw_log = None
    if arm == "T":
        gw_log = os.path.join(session_dir, "gw.jsonl")
        env2 = dict(os.environ, CR_GATEWAY_CACHE_ALIGN="gated")
        os.environ["CR_GATEWAY_CACHE_ALIGN"] = "gated"        # start_proxy copies os.environ
        try:
            proxy, port = start_proxy(cfg["repo"], cfg["python"], gw_log)
        finally:
            os.environ.pop("CR_GATEWAY_CACHE_ALIGN", None)
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        del env2
    rec["gw_log"] = gw_log
    try:
        sid = rec.get("session_id")
        for i, t in enumerate(tasks):
            key = f"chunk{i}"
            if rec.get(key, {}).get("done"):
                continue
            wt = worktree(cfg["mirror"], t["base_commit"],
                          os.path.join(session_dir, f"wt-{t['instance_id']}"))
            if i > 0 and not rec.get(f"gap{i}_done"):
                time.sleep(cfg.get("gap_s", GAP_S_DEFAULT))    # the real TTL-expiry window
                rec[f"gap{i}_done"] = True
            res = run_chunk(chunk_prompt(t, wt), session_dir, env, resume=sid,
                            model=cfg.get("model", "sonnet"), budget=cfg.get("budget", 2.5))
            sid = res.get("session_id") or sid
            rec["session_id"] = sid
            rec[key] = {"done": True, "cost_usd": res.get("total_cost_usd"),
                        "status": res.get("subtype"), "wt": wt}
            # grade this task now (B6 convention: reset official test files, then test_patch)
            reset_test_files(wt, t["test_patch"])
            if not apply_patch(wt, t["test_patch"]):
                rec[key]["grade"] = {"test_patch_applied": False, "success": False}
            else:
                g = grade(wt, t)
                g["test_patch_applied"] = True
                rec[key]["grade"] = g
            yield rec                                          # caller persists incrementally
    finally:
        if proxy is not None:
            proxy.terminate()


def predict(cfg_path=None):
    """Zero-quota preregistration: chain three real B6 native timelines with the B8 gaps and run
    the calibrated B7 policies over the chained stream. The live run's primary endpoint is judged
    against THIS predicted band."""
    from contextruntime.cachemodel import extract_calls, load_b6_sessions
    from corpus.b7_cache_replay import POLICIES, mutation_streams, run_policy
    picks = [("django__django-16485", "N0"), ("django__django-16527", "N0"),
             ("django__django-16901", "N0")]
    streams = []
    for tid, key, tp in load_b6_sessions("corpus/analysis/b6-live-results.json", "N"):
        for want_t, want_k in picks:
            if tid == want_t and key == want_k:
                calls = extract_calls(tp)
                events, think = mutation_streams(tp, len(calls))
                streams.append((calls, events, think))
    chained_calls, chained_events, chained_think = [], [], [0]
    P_base = 0
    t_base = 0.0
    n_base = 0
    for k, (calls, events, think) in enumerate(streams):
        warm = calls[0].read
        t_off = (t_base - calls[0].ts) + (GAP_S_DEFAULT if k else 0.0)
        for c in calls:
            chained_calls.append(type(c)(P=P_base + (c.P - warm), read=0, creation=0, input=c.input,
                                         out=c.out, ts=c.ts + t_off))
        for e in events:
            chained_events.append({"eligible": e["eligible"] + n_base, "turn": e["turn"] + n_base,
                                   "tokens": e["tokens"]})
        chained_think.extend(think[1:len(calls) + 1])
        P_base += calls[-1].P - warm
        t_base = chained_calls[-1].ts
        n_base += len(calls)
    chained_events.sort(key=lambda e: e["eligible"])
    out = {}
    for pol in POLICIES:
        r = run_policy(chained_calls, chained_events, chained_think, pol, warm=streams[0][0][0].read)
        out[pol] = {"bite": round(r["bite"]), "sum_P": r["sum_P"], "fires": r["fires"]}
    nat = out["native"]["bite"]
    for pol in POLICIES:
        out[pol]["bite_delta_pct"] = round(100 * (out[pol]["bite"] / nat - 1), 2)
    print(json.dumps(out, indent=1))
    return out


def main(cfg_path):
    cfg = json.load(open(cfg_path))
    out = {"pairs": {}}
    if os.path.exists(cfg["out"]):
        out = json.load(open(cfg["out"]))
    tasks = cfg["tasks"]
    for rep in range(cfg.get("reps", 2)):
        for arm in ("N", "T"):
            key = f"{arm}{rep}"
            rec = out["pairs"].setdefault(key, {})
            if rec.get("complete"):
                continue
            sdir = os.path.join(cfg["workdir"], f"session-{key}")
            for _ in run_session(arm, tasks, sdir, cfg, rec):
                json.dump(out, open(cfg["out"], "w"), indent=2)
            enc_ready = all(rec.get(f"chunk{i}", {}).get("done") for i in range(len(tasks)))
            if enc_ready:
                tp = _transcript_for_dir(sdir)
                rec["transcript"] = tp
                if tp:
                    rec["metrics"] = {k: v for k, v in transcript_metrics(tp).items() if k != "reads"}
                    rec["bite"] = bite_from_transcript(tp)
                rec["gateway"] = gw_fires(rec.get("gw_log"))
                rec["complete"] = True
            json.dump(out, open(cfg["out"], "w"), indent=2)
            print(f"{key}: complete={rec.get('complete')} bite={((rec.get('bite') or {}).get('bite'))} "
                  f"gw={rec.get('gateway')}", flush=True)
    print("wrote", cfg["out"])


def _transcript_for_dir(d):
    import glob
    import re
    enc = re.sub(r"[/_]", "-", os.path.abspath(d))
    files = glob.glob(os.path.expanduser(f"~/.claude/projects/*{enc}*/*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "predict":
        predict()
    else:
        main(sys.argv[1])
