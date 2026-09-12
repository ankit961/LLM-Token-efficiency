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


def chat(endpoint, model, messages, tools, *, timeout=180):
    """One OpenAI /v1/chat/completions call. Returns (message_dict, usage_dict, ttft_s)."""
    body = json.dumps({"model": model, "messages": messages, "tools": tools,
                       "temperature": 0, "stream": False}).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
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


def retire(messages):
    """Provable retirement on OpenAI format: for each supersession key, keep only the LAST tool
    result; stub the earlier ones with a recovery hint. Returns (new_messages, n_retired).
    tool_call args live on the assistant turn that requested them; the result is the next
    role:'tool' message with the matching tool_call_id."""
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
                nm = dict(m); nm["content"] = "[retired: superseded by a later call; re-read/re-run if needed]"
                out.append(nm); n += 1
                continue
        out.append(m)
    return out, n


def _exec_tool(name, args, wt):
    try:
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


def run_task(endpoint, model, task_prompt, wt, *, arm, max_steps=30, timeout=180):
    """Bounded agent loop. Returns metrics incl. Σ prompt_tokens (residency)."""
    tools = TREATMENT_TOOLS if arm == "T" else TOOLS
    messages = [
        {"role": "system", "content": "You are a coding agent. Use the tools to inspect and edit "
         "the repository, then reply DONE. Keep changes minimal."},
        {"role": "user", "content": task_prompt},
    ]
    sum_prompt = sum_completion = calls = retired = 0
    ttfts = []
    for _ in range(max_steps):
        send = messages
        if arm == "T":
            send, n = retire(messages)
            retired += n
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
                        "retired": retired, "ttft_median": sorted(ttfts)[len(ttfts) // 2] if ttfts else None,
                        "status": "done"}
            messages.append({"role": "user", "content": "Continue, or reply DONE if finished."})
            continue
        for tc in tcs:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:      # noqa: BLE001
                args = {}
            result = _exec_tool(tc["function"]["name"], args, wt)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    return {"calls": calls, "sum_prompt": sum_prompt, "sum_completion": sum_completion,
            "retired": retired, "status": "max_steps"}


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


def main(argv):
    if argv and argv[0] == "preflight":
        endpoint = argv[1] if len(argv) > 1 else os.environ.get("LOCAL_ENDPOINT", "http://100.120.148.39:11434")
        model = argv[2] if len(argv) > 2 else os.environ.get("LOCAL_MODEL", "")
        if not model:
            print("usage: preflight <endpoint> <model>  (or set LOCAL_MODEL)"); return 2
        r = preflight(endpoint, model)
        return 0 if r.get("preflight_pass") else 1
    print("modes: preflight <endpoint> <model>   (ab mode wired after preflight passes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
