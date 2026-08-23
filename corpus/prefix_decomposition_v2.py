#!/usr/bin/env python3
"""Prefix decomposition v2 — WHERE does the whole resident prefix go? ZERO quota.

B2.0 decomposed only the tool-OUTPUT slice (~16% of peak). To reason about a 50% target we need the
other ~84%. Transcripts do not contain the system prompt / tool definitions, but `usage` carries the
REAL per-turn prefix P_t = cache_read + cache_creation + input. Everything visible in the transcript
(user text, assistant text, tool_use inputs, tool_results, thinking) is tokenized; the residual
    fixed_t = P_t − visible_t
is the system prompt + tool definitions + injected context (CLAUDE.md, skills, memory …) — measured
without ever seeing it. Each component is reported in TOKEN-TURNS (Σ_t resident tokens at turn t),
i.e. its share of Σ P_t — the residency workload that is the real cost driver (B3 economics).

Waste patterns (token-turns they occupy): duplicate/refresh file reads, Edit inputs that duplicate
text already resident from a Read, polling turns (`sleep` in Bash), inline scripts (heredocs) carried
forever in tool_use inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict

from contextruntime.reducers.base import tokens as _tok
from corpus.transcript_util import merged_records

_READ = {"Read", "NotebookRead"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit"}


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def decompose(transcript_path):
    """Per-session decomposition. Returns component token-turns + waste patterns + usage totals."""
    comp = defaultdict(int)           # tokens ADDED to the prefix, by component (appear at a turn)
    added_at = []                     # (turn, component, tokens) to compute token-turns
    usage_P = {}                      # turn -> real P_t
    turn = 0
    uses = {}
    read_hash_seen, read_path_seen = {}, {}
    read_texts = []                   # normalized read contents (for edit-dup detection)
    waste = defaultdict(int)          # tokens by waste pattern
    waste_turns = defaultdict(set)    # turns that are pure waste (polling)
    first_P = None
    for rec in merged_records(transcript_path):
        m = rec.get("message") or {}
        content = m.get("content")
        rtype = rec.get("type")
        if rtype == "assistant" and isinstance(content, list):
            u = m.get("usage")
            if u:
                turn += 1
                P = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0)
                usage_P[turn] = P
                if first_P is None:
                    first_P = P
                # retained thinking is stored EMPTY (signature only) but stays resident on Opus 4.5+/Sonnet 4.6+:
                # estimate it as this call's output_tokens minus the visible output blocks
                vis_out = sum(_tok(b.get("text", "")) if b.get("type") == "text" else
                              _tok(json.dumps(b.get("input") or {})) if b.get("type") == "tool_use" else 0
                              for b in content if isinstance(b, dict))
                added_at.append((turn, "thinking(est,invisible)", max(u.get("output_tokens", 0) - vis_out, 0)))
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    t = _tok(b.get("text", ""))
                    added_at.append((turn, "assistant_text", t))
                elif bt == "thinking":
                    added_at.append((turn, "thinking", _tok(b.get("thinking", ""))))
                elif bt == "tool_use":
                    name, inp = b.get("name", ""), (b.get("input") or {})
                    uses[b.get("id")] = (name, inp, turn)
                    t = _tok(json.dumps(inp))
                    key = "tool_use_edit" if name in _EDIT else ("tool_use_write" if name == "Write"
                          else ("tool_use_bash" if name == "Bash" else "tool_use_other"))
                    added_at.append((turn, key, t))
                    cmd = inp.get("command", "") if name == "Bash" else ""
                    if name == "Bash" and re.search(r"\bsleep\s+\d+", cmd):
                        waste_turns["polling"].add(turn)
                    if name == "Bash" and "<<" in cmd and len(cmd) > 400:
                        waste["inline_script_input"] += t
                    if name in _EDIT:                      # edit text already resident from a Read?
                        olds = [inp.get("old_string", "")] + [e.get("old_string", "") for e in inp.get("edits", [])]
                        for o in olds:
                            no = _norm(o)
                            if len(no) > 40 and any(no in rt for rt in read_texts):
                                waste["edit_old_string_dup_of_read"] += _tok(o)
        elif rtype == "user" and content is not None:
            if isinstance(content, str):
                added_at.append((turn, "user_text", _tok(content)))
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        added_at.append((turn, "user_text", _tok(b.get("text", ""))))
                    elif b.get("type") == "tool_result":
                        ref = uses.get(b.get("tool_use_id"))
                        name = ref[0] if ref else "?"
                        txt = _text(b.get("content"))
                        t = _tok(txt)
                        key = ("result_read" if name in _READ else "result_bash" if name == "Bash"
                               else "result_search" if name in ("Grep", "Glob") else "result_edit"
                               if name in _EDIT or name == "Write" else "result_other")
                        added_at.append((turn, key, t))
                        if name in _READ and ref:
                            path = (ref[1] or {}).get("file_path", "")
                            h = hashlib.sha1(txt.encode()).hexdigest()
                            if h in read_hash_seen:
                                waste["read_exact_duplicate"] += t
                            elif path in read_path_seen:
                                waste["read_same_path_refresh"] += t
                            read_hash_seen[h] = turn
                            read_path_seen[path] = turn
                            read_texts.append(_norm(txt))
    T = max(turn, 1)
    # token-turns per component: each addition at turn c is resident for turns c..T (approx; ignores compaction)
    tt = defaultdict(int)
    for c, key, t in added_at:
        tt[key] += t * max(T - max(c, 1) + 1, 1)
    visible_tt = sum(tt.values())
    sum_P = sum(usage_P.values())
    fixed_tt = max(sum_P - visible_tt, 0)
    # polling waste = the whole prefix of each polling turn
    poll_tt = sum(usage_P.get(t, 0) for t in waste_turns["polling"])
    return {"turns": T, "sum_P": sum_P, "first_P": first_P or 0,
            "visible_tt": visible_tt, "fixed_tt": fixed_tt,
            "components_tt": dict(tt), "waste_tokens": dict(waste),
            "polling_turns": len(waste_turns["polling"]), "polling_tt": poll_tt,
            "multiwindow": (sum_P / T) > 210_000}


def aggregate(results):
    """Mean share of Σ P_t per component (+fixed), over sessions."""
    keys = set()
    for r in results:
        keys |= set(r["components_tt"])
    out = {"n": len(results)}
    shares = defaultdict(list)
    for r in results:
        if not r["sum_P"]:
            continue
        shares["fixed(system+tools+injected)"].append(r["fixed_tt"] / r["sum_P"])
        for k in keys:
            shares[k].append(r["components_tt"].get(k, 0) / r["sum_P"])
        shares["waste:polling_turns"].append(r["polling_tt"] / r["sum_P"])
    out["share_of_sum_P"] = {k: round(100 * sum(v) / len(v), 2) for k, v in shares.items() if v}
    out["mean_turns"] = round(sum(r["turns"] for r in results) / len(results), 1)
    out["mean_first_P"] = round(sum(r["first_P"] for r in results) / len(results))
    wt = defaultdict(list)
    for r in results:
        for k, v in r["waste_tokens"].items():
            wt[k].append(v)
    out["mean_waste_tokens"] = {k: round(sum(v) / len(results)) for k, v in wt.items()}
    out["mean_polling_turns"] = round(sum(r["polling_turns"] for r in results) / len(results), 2)
    return out


def _main(argv):
    res = json.load(open(argv[1]))
    tps = sorted({m["transcript"] for m in res.values()
                  if isinstance(m, dict) and m.get("transcript") and os.path.exists(m["transcript"])})
    rows = [decompose(tp) for tp in tps]
    single = [r for r in rows if not r["multiwindow"]]
    agg = aggregate(single)
    print(f"=== Full resident-prefix decomposition, {agg['n']} single-window sessions "
          f"(mean {agg['mean_turns']} turns, mean startup prefix {agg['mean_first_P']:,} tok) ===")
    for k, v in sorted(agg["share_of_sum_P"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<36} {v:6.2f}% of Σ P_t")
    print(f"  waste tokens/session: {agg['mean_waste_tokens']}  polling turns/session: {agg['mean_polling_turns']}")
    for tp in argv[2:]:
        r = decompose(tp)
        a = aggregate([r])
        print(f"\n=== extra: {os.path.basename(tp)[:8]} turns={r['turns']} startup={r['first_P']:,} "
              f"{'(MULTI-WINDOW)' if r['multiwindow'] else ''} ===")
        for k, v in sorted(a["share_of_sum_P"].items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {k:<36} {v:6.2f}%")
        print(f"  waste: {r['waste_tokens']}  polling_turns={r['polling_turns']} ({100*r['polling_tt']/max(r['sum_P'],1):.1f}% of Σ P_t)")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
