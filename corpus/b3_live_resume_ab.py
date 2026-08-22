#!/usr/bin/env python3
"""B3.3 — LIVE paired resume A/B for retroactive context retirement.

The offline work (B3.0/B3.1/B3.2) left one thing a mechanical proxy can't settle: when an obsolete tool
result is actually ABSENT, does the agent still reach the same fix, or does it break / thrash? This
harness answers it live with the only mechanism available on a subscription-auth machine (no API key):
hand-edit a resumable Claude Code transcript and `claude -p --resume` it.

Per task: truncate a real native session at cut turn t*, reconstruct the worktree by replaying the
edits up to t*, then emit two resumable variants with fresh UUID session ids — FULL (unchanged) and
RETIRED (the tool-result content of every B3-retired object stubbed) — resume both with the same
continuation prompt (budget-capped), and compare downstream: does RETIRED edit the same files, and does
it have to re-read the objects we retired?

The pure functions here (truncation, retirement selection, stub emission, edit replay, continuation
analysis) are unit-tested; `run_pair` performs the live `claude -p` calls and is not exercised in CI.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid

from corpus.b3_context_retirement import _obj_key, assign_obsolescence

_READ = {"Read", "NotebookRead"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_KEYED = {"Grep", "Glob", "Bash"}
LAG = 5
STUB = ("[Context note: this earlier tool output was retired to save context. "
        "Re-run the tool or re-read the file if you need it again.]")
CONT = ("Continue resolving this issue. Implement the remaining source-code changes needed to fix the "
        "bug described at the start, then stop. Do not re-run the full test suite.")
_DROP_TYPES = {"last-prompt", "queue-operation"}


def load_records(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def turns_of(records):
    """Assign a turn index to each record (increments on an assistant record carrying usage)."""
    t, out = 0, []
    for r in records:
        if r.get("type") == "assistant" and (r.get("message") or {}).get("usage"):
            t += 1
        out.append(t)
    return out


def objects_and_edits(records, turns):
    """(objects, edits): objects = per tool_result {turn,key,path,tuid}; edits = (turn,tool,input)."""
    uses, objs, edits = {}, [], []
    for r, tt in zip(records, turns):
        m = r.get("message") or {}
        c = m.get("content")
        if r.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    n, inp = b.get("name", ""), (b.get("input") or {})
                    if n in _READ or n in _EDIT or n in _KEYED:
                        uses[b.get("id")] = (n, _obj_key(n, inp), tt, inp.get("file_path"))
                    if n in ("Edit", "MultiEdit", "Write"):
                        edits.append((tt, n, inp))
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    ref = uses.get(b.get("tool_use_id"))
                    if ref:
                        objs.append({"turn": ref[2], "key": ref[1], "path": ref[3], "tuid": b.get("tool_use_id")})
    return objs, edits


def clean_cut(records, turns, tstar):
    """Largest record index at turn<=tstar that is a message record with NO open (unresulted) tool_use
    after it — a safe truncation boundary."""
    openset, cut = set(), None
    for i, (r, tt) in enumerate(zip(records, turns)):
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    openset.add(b.get("id"))
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    openset.discard(b.get("tool_use_id"))
        if tt <= tstar and not openset and r.get("type") in ("assistant", "user"):
            cut = i
    return cut


def retired_by(objects, tstar, total_turns, *, lag=LAG):
    """(tuids, paths) for objects B3 would have retired by t*: superseded within-prefix at obsolete_turn,
    or an abandoned tail at tail_turn+lag, with the retire turn <= t*."""
    assign_obsolescence(objects, total_turns)
    tuids, paths = set(), set()
    for o in objects:
        rt = o["obsolete_turn"] if o["obsolete_turn"] is not None else (
            o["tail_turn"] + lag if o["tail_turn"] is not None else None)
        if rt is not None and rt <= tstar and o["turn"] <= tstar:
            tuids.add(o["tuid"])
            if o["path"]:
                paths.add(o["path"])
    return tuids, paths


def emit_variant(kept, sid, retired_tuids, stub):
    """Return kept records with the sessionId rewritten and (if stub) retired tool_result content
    replaced by the STUB. Pure — caller writes to disk."""
    out = []
    for r in kept:
        r = json.loads(json.dumps(r))
        r["sessionId"] = sid
        c = (r.get("message") or {}).get("content")
        if stub and isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in retired_tuids:
                    b["content"] = STUB
        out.append(r)
    return out


def apply_edits(worktree, edits_upto):
    """Replay Edit/MultiEdit/Write onto the worktree to reconstruct the state at t*."""
    for _tt, n, inp in edits_upto:
        fp = inp.get("file_path")
        if not fp:
            continue
        try:
            if n == "Write":
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                open(fp, "w").write(inp.get("content", ""))
            elif n == "Edit":
                s = open(fp).read()                       # read BEFORE opening for write (truncation)
                open(fp, "w").write(s.replace(inp["old_string"], inp["new_string"], 1))
            elif n == "MultiEdit":
                s = open(fp).read()
                for e in inp.get("edits", []):
                    s = s.replace(e["old_string"], e["new_string"], 1)
                open(fp, "w").write(s)
        except Exception as e:      # noqa: BLE001
            print(f"  [replay warn] {n} {os.path.basename(fp)}: {e}")


def analyze_continuation(records, src_uuids, retired_paths):
    """Continuation = records whose uuid was NOT in the source prefix. Report re-reads of retired paths
    and files edited."""
    cont = [r for r in records if r.get("uuid") not in src_uuids]
    rereads, edits, tools = [], [], []
    for r in cont:
        for b in ((r.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                n, fp = b.get("name", ""), (b.get("input") or {}).get("file_path", "")
                tools.append(n)
                if n in _READ and fp in retired_paths:
                    rereads.append(os.path.basename(fp))
                if n in ("Edit", "MultiEdit", "Write"):
                    edits.append(os.path.basename(fp))
    return {"cont_records": len(cont), "n_tools": len(tools),
            "reread_retired": len(rereads), "reread_files": sorted(set(rereads)),
            "edited": sorted(set(edits))}


def _git_reset(worktree, base):
    subprocess.run(["git", "-C", worktree, "checkout", "--", "."], capture_output=True)
    subprocess.run(["git", "-C", worktree, "clean", "-fdq"], capture_output=True)
    subprocess.run(["git", "-C", worktree, "reset", "-q", "--hard", base], capture_output=True)


def _resume(worktree, sid, budget, cont=CONT):
    p = subprocess.run(["claude", "-p", "--resume", sid, cont, "--output-format", "json",
                        "--max-budget-usd", str(budget), "--dangerously-skip-permissions", "--model", "sonnet"],
                       cwd=worktree, capture_output=True, text=True, timeout=1200)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:      # noqa: BLE001
        return {"parse_error": p.stdout[-400:], "stderr": p.stderr[-200:]}


def run_pair(cfg):
    """LIVE. cfg keys: src, projdir, worktree, base, tstar, budget, task. Returns the paired result."""
    recs = load_records(cfg["src"])
    turns = turns_of(recs)
    T = max(turns)
    objs, edits = objects_and_edits(recs, turns)
    cut = clean_cut(recs, turns, cfg["tstar"])
    kept = [r for r in recs[:cut + 1] if r.get("type") not in _DROP_TYPES]
    tuids, paths = retired_by(objs, cfg["tstar"], T)
    edits_upto = [(tt, n, inp) for (tt, n, inp) in edits if tt <= cfg["tstar"]]
    src_uuids = {r.get("uuid") for r in recs}
    out = {"task": cfg["task"], "T": T, "tstar": cfg["tstar"], "kept": len(kept), "n_retired": len(tuids),
           "retired_py": sorted(os.path.basename(p) for p in paths if str(p).endswith(".py"))}
    for arm, stub in (("FULL", False), ("RETIRED", True)):
        sid = str(uuid.uuid4())
        variant = emit_variant(kept, sid, tuids, stub)
        open(os.path.join(cfg["projdir"], sid + ".jsonl"), "w").write("\n".join(json.dumps(r) for r in variant) + "\n")
        _git_reset(cfg["worktree"], cfg["base"])
        apply_edits(cfg["worktree"], edits_upto)
        res = _resume(cfg["worktree"], sid, cfg["budget"])
        after = load_records(os.path.join(cfg["projdir"], sid + ".jsonl"))
        out[arm] = {"cost_usd": res.get("total_cost_usd"), "terminal": res.get("terminal_reason") or res.get("subtype"),
                    **analyze_continuation(after, src_uuids, paths)}
    out["same_fix"] = out["FULL"]["edited"] == out["RETIRED"]["edited"]
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(run_pair(json.loads(sys.argv[1])), indent=2))
