#!/usr/bin/env python3
"""B9 — local-SLM arm: an OpenAI-compatible coding-agent loop with native vs treatment.

WHY a new harness: our gateway and Claude Code both speak the Anthropic Messages format; a local
Ollama/vLLM server speaks the OpenAI chat/completions format (tool_calls / role:"tool"). Routing
Claude Code through a shape-translator is the fragile path the release plan warns against. Instead
this is the "common controllable harness": a small agent loop that talks OpenAI to the local
endpoint, with our admission + retirement levers applied IN THE LOOP, comparing native-local vs
treatment-local WITHIN the same model. Cross-client (local vs Claude) differences mix harness and
model and are reported as such, never as a pure model effect.

Metric: residency = Σ prompt_tokens over the session (local serving has no cache-$ split — see
providers.local-serving; the axis is prefill work / TTFT, not dollars). Task success is graded on
the edited django worktree with the existing corpus.b6_grading. Stdlib only.

  native     full history every call; the model's own tool list
  treatment  ADMISSION (lean tool set) + RETIREMENT (a later tool call with the same key
             supersedes the earlier result → its content is stubbed with a recovery hint)

Modes:
  preflight  one trivial task, native — proves the model can tool-call, edit a file, and stop
  ab         the paired A/B over the configured django tasks
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Overwrite a file with new content.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_bash", "description": "Run a shell command in the repo.",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
]
# ADMISSION: the treatment arm ships only the tools the task needs. Here the whole set is already
# lean (3 tools); admission on a local model is the same lever, expressed as "no unused schemas".
TREATMENT_TOOLS = TOOLS
# RECOVERY: the recover tool pages the exact bytes of a retired result back in (its id is shown in
# the [retired: ... recover(id='...')] stub). It is part of the TREATMENT package — an opt-in
# affordance (run_task(..., recover=True)) that makes retirement provably lossless AT SOURCE:
# nothing the model saw is destroyed, only moved out of the active prefix until asked back.
RECOVER_TOOL = {"type": "function", "function": {
    "name": "recover",
    "description": "Restore the exact content of a tool result that was retired. Pass the id shown "
                   "in its [retired: ... recover(id='...')] placeholder.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}


def chat(endpoint, model, messages, tools, *, timeout=180):
    """One OpenAI /v1/chat/completions call. Returns (message_dict, usage_dict, ttft_s)."""
    body = json.dumps({"model": model, "messages": messages, "tools": tools,
                       "temperature": 0, "stream": False}).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("LOCAL_API_KEY")           # e.g. LiteLLM master key; never hardcoded/logged
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(endpoint.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    ttft = time.time() - t0
    return d["choices"][0]["message"], d.get("usage") or {}, ttft


def _obj_key(name, args):
    """Supersession key: repeated read/write of the same path, or the same bash command."""
    if name in ("read_file", "write_file"):
        return "path:" + os.path.normpath(str(args.get("path", "")))
    if name == "run_bash":
        return "bash:" + (args.get("cmd") or "").strip()
    return ""


def retire(messages, store=None):
    """Provable retirement on OpenAI format: for each supersession key, keep only the LAST tool
    result; stub the earlier ones with a recovery hint. Returns (new_messages, n_retired).
    tool_call args live on the assistant turn that requested them; the result is the next
    role:'tool' message with the matching tool_call_id.

    When `store` (a dict) is given, the verbatim content of each result being stubbed is saved under
    its tool_call_id BEFORE stubbing and the stub names that id — so a recover(id=...) tool can page
    the exact original back in (provably lossless at source). With store=None (default) the stub is
    the plain re-read hint, byte-identical to the pre-recover behavior."""
    key_by_id = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except Exception:      # noqa: BLE001
                    args = {}
                key_by_id[tc["id"]] = _obj_key(tc["function"]["name"], args)
    last_idx = {}                                          # key -> index of its latest tool msg
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            k = key_by_id.get(m.get("tool_call_id"), "")
            if k:
                last_idx[k] = i
    out, n = [], 0
    for i, m in enumerate(messages):
        if m.get("role") == "tool":
            k = key_by_id.get(m.get("tool_call_id"), "")
            if k and last_idx.get(k) != i and not str(m.get("content", "")).startswith("[retired"):
                tcid = m.get("tool_call_id")
                if store is not None:
                    store[tcid] = m.get("content", "")      # keep the exact bytes for recover(id)
                    hint = (f"[retired: superseded by a later call; call recover(id='{tcid}') to "
                            "restore this exact result, or re-read/re-run]")
                else:
                    hint = "[retired: superseded by a later call; re-read/re-run if needed]"
                nm = dict(m); nm["content"] = hint
                out.append(nm); n += 1
                continue
        out.append(m)
    return out, n


def _exec_tool(name, args, wt, store=None):
    try:
        if name == "recover":
            if store is None:
                return "error: recover is not enabled in this run"
            rid = str(args.get("id", ""))
            return store.get(rid, f"error: nothing retired under id {rid!r}")
        if name == "read_file":
            p = os.path.join(wt, args["path"]) if not os.path.isabs(args["path"]) else args["path"]
            return open(p, encoding="utf-8", errors="replace").read()[:20000]
        if name == "write_file":
            p = os.path.join(wt, args["path"]) if not os.path.isabs(args["path"]) else args["path"]
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            open(p, "w", encoding="utf-8").write(args["content"])
            return "written"
        if name == "run_bash":
            r = subprocess.run(args["cmd"], shell=True, cwd=wt, capture_output=True, text=True, timeout=120)
            return (r.stdout + r.stderr)[-6000:]
    except Exception as e:      # noqa: BLE001
        return f"error: {e}"
    return "unknown tool"


def run_task(endpoint, model, task_prompt, wt, *, arm, max_steps=30, timeout=180, recover=False):
    """Bounded agent loop. Returns metrics incl. Σ prompt_tokens (residency). `recover=True` (only
    meaningful on arm T) adds the recover tool and a per-run store so retired results are restorable
    by id — provably lossless at source; it does not change the default A/B (recover defaults off)."""
    use_recover = recover and arm == "T"
    tools = list(TREATMENT_TOOLS if arm == "T" else TOOLS)
    if use_recover:
        tools = tools + [RECOVER_TOOL]           # recovery affordance is part of the treatment
    store = {} if use_recover else None
    messages = [
        {"role": "system", "content": "You are a coding agent. Use the tools to inspect and edit "
         "the repository, then reply DONE. Keep changes minimal."},
        {"role": "user", "content": task_prompt},
    ]
    sum_prompt = sum_completion = calls = retired = 0
    ttfts = []
    for _ in range(max_steps):
        if arm == "T":
            # Persist retirement into history: a stubbed result STAYS stubbed (byte-stable — the
            # same invariant the gateway keeps for cache alignment). retire() is idempotent, so n
            # counts only the results newly retired THIS turn, making `retired` the true DISTINCT
            # count. (The earlier code retired a throwaway copy and left `messages` full, so every
            # standing stub was re-counted each turn and `retired` over-reported.)
            messages, n = retire(messages, store=store)
            retired += n
        send = messages
        try:
            msg, usage, ttft = chat(endpoint, model, send, tools, timeout=timeout)
        except Exception as e:      # noqa: BLE001
            return {"error": str(e)[:200], "calls": calls, "sum_prompt": sum_prompt,
                    "sum_completion": sum_completion, "retired": retired, "status": "error"}
        calls += 1
        sum_prompt += usage.get("prompt_tokens", 0)
        sum_completion += usage.get("completion_tokens", 0)
        ttfts.append(ttft)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        tcs = msg.get("tool_calls") or []
        if not tcs:
            if "DONE" in (msg.get("content") or "") or calls >= max_steps:
                return {"calls": calls, "sum_prompt": sum_prompt, "sum_completion": sum_completion,
                        "retired": retired, "recoverable": len(store) if store is not None else 0,
                        "ttft_median": sorted(ttfts)[len(ttfts) // 2] if ttfts else None,
                        "status": "done"}
            messages.append({"role": "user", "content": "Continue, or reply DONE if finished."})
            continue
        for tc in tcs:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:      # noqa: BLE001
                args = {}
            result = _exec_tool(tc["function"]["name"], args, wt, store=store)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    return {"calls": calls, "sum_prompt": sum_prompt, "sum_completion": sum_completion,
            "retired": retired, "recoverable": len(store) if store is not None else 0,
            "status": "max_steps"}


def preflight(endpoint, model):
    """Zero-django smoke: can the model tool-call, write a file, and stop? Gate before the A/B."""
    import tempfile
    wt = tempfile.mkdtemp(prefix="b9pf-")
    open(os.path.join(wt, "hello.txt"), "w").write("old\n")
    r = run_task(endpoint, model,
                 "Overwrite hello.txt so its only contents are the word NEW, then reply DONE.",
                 wt, arm="N", max_steps=8)
    ok = r.get("status") == "done" and open(os.path.join(wt, "hello.txt")).read().strip() == "NEW"
    r["preflight_pass"] = ok
    print(json.dumps(r, indent=1))
    return r


def worktree(mirror, base, dest):
    """Fresh detached git worktree at base_commit. Kept after the run (disk is cheap) so any rep
    can be re-graded or inspected — the same convention as corpus/b6_live_ab.py."""
    subprocess.run(["git", "-C", mirror, "worktree", "remove", "--force", dest], capture_output=True)
    subprocess.run(["git", "-C", mirror, "worktree", "add", "--detach", dest, base], capture_output=True)
    if not os.path.isdir(dest):
        raise RuntimeError(f"worktree failed: {dest} (is 'mirror' a django git dir with that commit?)")
    return dest


def _summarize(tasks):
    """Pool residency (Σ prompt_tokens) and graded successes per arm; report the T-vs-N delta.
    Residency is the honest local-serving cost axis — no cache-$ split (see providers.local-serving)."""
    agg = {"N": {"sum_prompt": 0, "success": 0, "graded": 0, "reps": 0},
           "T": {"sum_prompt": 0, "success": 0, "graded": 0, "reps": 0, "retired": 0, "recoverable": 0}}
    for reps in tasks.values():
        for key, rec in reps.items():
            arm = rec.get("arm") or key[:1]
            a = agg.get(arm)
            if a is None:
                continue
            m = rec.get("metrics") or {}
            g = rec.get("grade") or {}
            a["sum_prompt"] += m.get("sum_prompt", 0) or 0
            a["reps"] += 1
            if "success" in g:
                a["graded"] += 1
                a["success"] += 1 if g.get("success") else 0
            if arm == "T":
                a["retired"] += m.get("retired", 0) or 0
                a["recoverable"] += m.get("recoverable", 0) or 0
    n, t = agg["N"]["sum_prompt"], agg["T"]["sum_prompt"]
    agg["pooled_residency_delta_pct"] = round((t - n) / n * 100, 1) if n else None
    return agg


def ab(cfg):
    """Paired native-vs-treatment A/B over django SWE-bench tasks against ONE local model.

    Same model on both arms, so the delta is the harness's admission + retirement levers, never a
    model change (the cross-client caveat in the module docstring does not apply here). The cost
    axis is residency = Σ prompt_tokens; grading is the real B6 native grader (reset the official
    test files the agent may have touched, apply the task's test_patch, run FAIL_TO_PASS +
    PASS_TO_PASS under python3.11). A rep is a SUCCESS only if F2P passes and P2P stays green.

    cfg: endpoint, model, mirror (a django git dir with the base commits), workdir, out (results
         json), tasks[] (each: instance_id, base_commit, problem, test_patch, FAIL_TO_PASS,
         PASS_TO_PASS), and optional reps (3), max_steps (30), timeout (180).
    Resumable: existing (task, rep, arm) records in `out` are kept and skipped.
    """
    from corpus.b6_grading import apply_patch, grade, reset_test_files   # local grader; lazy import

    endpoint, model = cfg["endpoint"], cfg["model"]
    reps, max_steps, timeout = cfg.get("reps", 3), cfg.get("max_steps", 30), cfg.get("timeout", 180)
    os.makedirs(cfg["workdir"], exist_ok=True)
    out = json.load(open(cfg["out"])) if os.path.exists(cfg["out"]) else {}
    out.setdefault("model", model)
    out.setdefault("endpoint", endpoint)
    out.setdefault("tasks", {})
    for task in cfg["tasks"]:
        iid = task["instance_id"]
        out["tasks"].setdefault(iid, {})
        for rep in range(reps):
            for arm in ("N", "T"):
                key = f"{arm}{rep}"
                if out["tasks"][iid].get(key):
                    continue
                wt = worktree(cfg["mirror"], task["base_commit"],
                              os.path.join(cfg["workdir"], f"{iid}-{key}"))
                prompt = (task["problem"] + "\n\nWork in this repository at the current commit; "
                          "implement a fix for the issue above. Run relevant tests if useful. "
                          "Reply DONE when finished.")
                m = run_task(endpoint, model, prompt, wt, arm=arm, max_steps=max_steps,
                             timeout=timeout, recover=cfg.get("recover", False))
                rec = {"arm": arm, "rep": rep, "metrics": m}
                # Real grading on the edited tree (SWE-bench convention): reset the official test
                # files the agent may have touched, then apply the task's test_patch and run tests.
                reset_test_files(wt, task["test_patch"])
                if not apply_patch(wt, task["test_patch"]):
                    rec["grade"] = {"test_patch_applied": False, "success": False}
                else:
                    g = grade(wt, task)
                    g["test_patch_applied"] = True
                    rec["grade"] = g
                out["tasks"][iid][key] = rec
                json.dump(out, open(cfg["out"], "w"), indent=2)
                print(f"{iid} {key}: status={m.get('status')} calls={m.get('calls')} "
                      f"sum_prompt={m.get('sum_prompt')} retired={m.get('retired')} "
                      f"success={rec['grade'].get('success')}", flush=True)
    out["summary"] = _summarize(out["tasks"])
    json.dump(out, open(cfg["out"], "w"), indent=2)
    print("SUMMARY", json.dumps(out["summary"]))
    print("wrote", cfg["out"])
    return out


def main(argv):
    if argv and argv[0] == "preflight":
        endpoint = argv[1] if len(argv) > 1 else os.environ.get("LOCAL_ENDPOINT", "http://100.120.148.39:11434")
        model = argv[2] if len(argv) > 2 else os.environ.get("LOCAL_MODEL", "")
        if not model:
            print("usage: preflight <endpoint> <model>  (or set LOCAL_MODEL)"); return 2
        r = preflight(endpoint, model)
        return 0 if r.get("preflight_pass") else 1
    if argv and argv[0] == "ab":
        if len(argv) < 2:
            print("usage: ab <config.json>\n"
                  "  config keys: endpoint, model, mirror, workdir, out, tasks[]  "
                  "(reps/max_steps/timeout optional)")
            return 2
        cfg = json.load(open(argv[1]))
        cfg.setdefault("endpoint", os.environ.get("LOCAL_ENDPOINT", "http://127.0.0.1:11434"))
        cfg.setdefault("model", os.environ.get("LOCAL_MODEL", ""))
        if not cfg.get("model"):
            print("config needs 'model' (or set LOCAL_MODEL)"); return 2
        ab(cfg)
        return 0
    print("modes:\n  preflight <endpoint> <model>   smoke: tool-call, edit a file, stop\n"
          "  ab <config.json>               paired A/B over django tasks, real B6 grading")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
