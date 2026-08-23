#!/usr/bin/env python3
"""B5.1 — Discovery Call-Collapse Oracle. ZERO model quota. No executor is built.

Decides whether the multi-call discovery trajectories that dominate coding sessions can be
deterministically collapsed into fewer model calls WITHOUT hiding information the model needed between
those calls. This is the measurement that determines whether the ~50% stack is honest
(`docs/prefix-doctor-findings.md` multiplicative arithmetic: ≥~15–20% safe call reduction ⇒ credible).

Unit of analysis: ONE real API call (requestId-merged via `transcript_util.merged_records`), validated
against the CLI-reported `num_turns`/usage where available.

Discovery (conservative): calls whose external actions are principally Read/Grep/Glob/NotebookRead or
read-only Bash (ls/find/cat/head/tail/grep/rg/git log|show|diff|status|blame/wc/tree/sed -n/awk).
Everything else — tests/execution, edits/writes, other (state-changing) Bash, no-tool — breaks a run.

Transitions inside a run are classified mechanically:
  D0  mechanically predetermined — every target (path/pattern) of the next call appears verbatim in
      the PREVIOUS call's output (grep hit → read that file; listing → read entry; traceback →
      read frame; continuation read of the same path).
  D1  locally programmable — every target appears in the run's ACCUMULATED inputs+outputs (including
      the seed call's inputs), or is a bounded mechanical expansion of them (same-dir sibling,
      dirname, test_<name>/<name>_test mapping, identifier extracted from prior output), or the call
      is a parameter-free/read-only inspection a local program could always run.
  D2  model-dependent — some target is not derivable from the run's own history (the model synthesized
      it from reasoning or from context outside the run).
A run is D0-collapsible if ALL its transitions are D0; D0+D1-collapsible if all ∈ {D0, D1}. Runs of
length 1 save nothing and are excluded from savings (still reported).

Evidence packet (offline reconstruction, collapsible runs only): the DEDUPLICATED union of what the
run actually returned — per path the LATEST read content, plus non-read outputs deduped by hash.
Built strictly from the run's own outputs; nothing from the future. Retention is then measured against
the next state-changing action: R_edit_target (path), R_edit_region (the edit's distinctive old_string
lines ⊆ packet text), R_next_action (edit: path∧region; test/exec: every path-like token of the
command present in packet or run history).

Savings are prefix-weighted with REAL per-call P_t: a collapsed n-call run keeps its first call (which
becomes the packet call) and avoids calls 2..n, saving exactly Σ P_t over those calls (the packet
content ≈ the same outputs that would have been resident anyway, deduplicated — so no added residency;
dedup is reported as a bonus, not counted). Avoided output tokens = Σ output_tokens of calls 2..n.

Threat to validity, measured not hand-waved: `choice_breadth` — for D0 transitions, how many candidate
paths the previous output offered vs which the model picked (top-k coverage). A real executor must
over-fetch to cover model choice; large breadth with deep picks weakens the D0 claim.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

from corpus.transcript_util import merged_records

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def tok(s):
        return len(_enc.encode(s or "", disallowed_special=()))
except Exception:      # noqa: BLE001
    from contextruntime.reducers.base import tokens as tok

_READ = {"Read", "NotebookRead"}
_SEARCH = {"Grep", "Glob"}
_EDIT = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
READONLY_BASH = re.compile(
    r"^\s*(ls|find|cat|head|tail|tree|wc|file|stat|grep|rg|ag|sed\s+-n|awk|"
    r"git\s+(log|show|diff|status|blame|grep|ls-files))\b")
TEST_BASH = re.compile(r"(pytest|tox\b|runtests|manage\.py\s+test|python[0-9.]*\s+-m\s+(pytest|unittest)|"
                       r"python[0-9.]*\s+\S*test\S*\.py|npm\s+test|cargo\s+test|go\s+test)")
_MUTATES = re.compile(r"(>>?\s|\|\s*tee\b|\b(rm|mv|cp|mkdir|touch|chmod|chown)\b|-delete\b)")
_PATH = re.compile(r"[\w./-]*/[\w./-]+\.[A-Za-z]{1,5}|[\w-]+\.py\b")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


# --------------------------------------------------------------------------- parsing
def classify_call(tools):
    """Conservative call class from its tool uses. Any state-changing action dominates."""
    if not tools:
        return "no_tool"
    kinds = set()
    for name, inp in tools:
        if name in _EDIT:
            kinds.add("edit")
        elif name in _READ or name in _SEARCH:
            kinds.add("discovery")
        elif name == "Bash":
            cmd = (inp.get("command") or "").strip()
            if TEST_BASH.search(cmd):
                kinds.add("test_exec")
            elif READONLY_BASH.match(cmd) and not _MUTATES.search(cmd):
                kinds.add("discovery")
            else:
                kinds.add("other_bash")
        else:
            kinds.add("other_tool")
    for dominant in ("edit", "test_exec", "other_bash", "other_tool"):
        if dominant in kinds:
            return dominant
    return "discovery"


def parse_session(transcript_path):
    """[{idx, cls, tools, out_text, P, out_tokens}] — one entry per REAL API call; each call's
    out_text is the concatenated tool_results its tool_uses produced."""
    calls = []
    pending_use = {}                                   # tool_use_id -> call idx
    for rec in merged_records(transcript_path):
        if rec.get("isSidechain"):
            continue                                    # subagent side-sessions are separate API traffic
        m = rec.get("message") or {}
        content = m.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list) and m.get("usage"):
            u = m["usage"]
            tools = [(b.get("name", ""), b.get("input") or {}) for b in content
                     if isinstance(b, dict) and b.get("type") == "tool_use"]
            calls.append({"idx": len(calls) + 1, "cls": classify_call(tools), "tools": tools,
                          "out_text": "",
                          "P": u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0),
                          "out_tokens": u.get("output_tokens", 0)})
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    pending_use[b.get("id")] = len(calls) - 1
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending_use:
                    i = pending_use[b.get("tool_use_id")]
                    c = b.get("content")
                    txt = c if isinstance(c, str) else "".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))
                    calls[i]["out_text"] += ("\n" + txt) if txt else ""
    return calls


# --------------------------------------------------------------------------- targets + derivability
def targets_of(call):
    """(paths, patterns, generic): what the call ASKS FOR. generic=True ⇒ parameter-free/read-only
    inspection a local program could always run (git status, plain ls…)."""
    paths, patterns = set(), set()
    generic = False
    for name, inp in call["tools"]:
        if name in _READ:
            p = inp.get("file_path") or inp.get("notebook_path")
            if p:
                paths.add(_norm(p))
        elif name == "Grep":
            if inp.get("pattern"):
                patterns.add(inp["pattern"])
            if inp.get("path"):
                paths.add(_norm(inp["path"]))
        elif name == "Glob":
            if inp.get("pattern"):
                patterns.add(inp["pattern"])
        elif name == "Bash":
            cmd = inp.get("command") or ""
            toks = _PATH.findall(cmd)
            for t in toks:
                paths.add(_norm(t))
            if not toks and READONLY_BASH.match(cmd.strip()) and len(cmd.split()) <= 3:
                generic = True
    return paths, patterns, generic


def _norm(p):
    return os.path.normpath(str(p)).lstrip("./")


def _mech_expansions(path, hist_paths):
    """Bounded mechanical relations: same dir, dirname, test-file mapping."""
    d = os.path.dirname(path)
    base = os.path.basename(path)
    stem = re.sub(r"\.py$", "", base)
    rel = {d}
    for hp in hist_paths:
        if os.path.dirname(hp) == d:
            rel.add(hp)
    rel.add(os.path.join(d, f"test_{base}"))
    rel.add(os.path.join(d, f"{stem}_test.py"))
    return rel


def transition_class(next_call, prev_out, hist_text, hist_paths):
    """D0 / D1 / D2 for one transition inside a run (see module docstring)."""
    paths, patterns, generic = targets_of(next_call)
    if generic and not paths and not patterns:
        return "D1"
    if not paths and not patterns:
        return "D1"                                     # no extractable novel parameters
    def in_text(t, text):
        return bool(t) and (t in text or os.path.basename(t) in text)
    if all(in_text(p, prev_out) for p in paths) and all(pt in prev_out or _ident_in(pt, prev_out) for pt in patterns):
        return "D0"
    ok = True
    for p in paths:
        if in_text(p, hist_text):
            continue
        if any(p in _mech_expansions(hp, hist_paths) or hp in _mech_expansions(p, hist_paths) for hp in hist_paths):
            continue
        ok = False
        break
    if ok:
        for pt in patterns:
            if pt in hist_text or _ident_in(pt, hist_text):
                continue
            ok = False
            break
    return "D1" if ok else "D2"


def _ident_in(pattern, text):
    """A grep pattern is derivable if its identifier-like core appears in the text."""
    ids = _IDENT.findall(pattern)
    return bool(ids) and all(i in text for i in ids)


def choice_breadth(next_call, prev_out):
    """(candidates_in_prev_output, rank_of_chosen) for the next call's first path target — the
    over-fetch a real executor would need. rank is 1-based position among candidate paths in output
    order; None if not applicable."""
    paths, _, _ = targets_of(next_call)
    if not paths:
        return None
    cands = []
    for mline in prev_out.splitlines():
        for c in _PATH.findall(mline):
            c = _norm(c)
            if c not in cands:
                cands.append(c)
    if not cands:
        return None
    for p in paths:
        for i, c in enumerate(cands):
            if p == c or p.endswith("/" + c) or c.endswith("/" + os.path.basename(p)):
                return {"candidates": len(cands), "rank": i + 1}
    return {"candidates": len(cands), "rank": None}


# --------------------------------------------------------------------------- runs + packets
def segment_runs(calls):
    runs, cur = [], []
    for c in calls:
        if c["cls"] == "discovery":
            cur.append(c)
        else:
            if cur:
                runs.append({"calls": cur, "next": c})
            cur = []
    if cur:
        runs.append({"calls": cur, "next": None})
    return runs


def build_packet(run_calls):
    """Deduplicated union of the run's own outputs: per read-path the LATEST content; other outputs
    deduped by hash. Returns (packet_text, packet_tokens, raw_tokens)."""
    import hashlib
    per_path, others, seen = {}, [], set()
    raw = 0
    for c in run_calls:
        raw += tok(c["out_text"])
        read_paths = [p for name, inp in c["tools"] if name in _READ
                      for p in [inp.get("file_path") or inp.get("notebook_path")] if p]
        if read_paths and c["out_text"]:
            per_path[_norm(read_paths[0])] = c["out_text"]
        elif c["out_text"]:
            h = hashlib.sha1(c["out_text"].encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                others.append(c["out_text"])
    text = "\n".join(list(per_path.values()) + others)
    return text, tok(text), raw


_LINENO = re.compile(r"^\s*\d+\t")


def _distinct_lines(s, minchars=20):
    out = set()
    for ln in (s or "").splitlines():
        t = _LINENO.sub("", ln).strip()
        if len(re.sub(r"\s+", "", t)) >= minchars:
            out.add(t)
    return out


def retention(run, packet_text):
    """Against the next state-changing action. A retention FAILURE is evidence that was present in the
    run's RAW outputs but is missing from the packet (the collapse lost it). Evidence the run never
    contained is NEUTRAL — it came from prior context, which a collapse leaves untouched — and is
    reported as provenance instead. Returns dict or None when not gateable."""
    nxt = run["next"]
    if nxt is None:
        return None
    raw_text = "\n".join(c["out_text"] for c in run["calls"])
    run_paths = {p for c in run["calls"] for p in targets_of(c)[0]}
    def present(p, text):
        return p in text or os.path.basename(p) in text
    if nxt["cls"] == "edit":
        paths, lost_region, lost_path, from_run = [], False, False, False
        pk_lines = _distinct_lines(packet_text)
        raw_lines = _distinct_lines(raw_text)
        for name, inp in nxt["tools"]:
            if name in _EDIT:
                p = inp.get("file_path")
                if p:
                    p = _norm(p)
                    paths.append(p)
                    in_raw = present(p, raw_text) or p in run_paths
                    in_pkt = present(p, packet_text) or p in run_paths
                    from_run = from_run or in_raw
                    if in_raw and not in_pkt:
                        lost_path = True
                olds = [inp.get("old_string", "")] + [e.get("old_string", "") for e in inp.get("edits", [])]
                for o in olds:
                    dl = _distinct_lines(o)
                    if dl and (dl & raw_lines):
                        from_run = True
                        if not (dl & pk_lines):
                            lost_region = True
        if not paths:
            return None
        return {"kind": "edit", "evidence_from_this_run": from_run,
                "edit_target_in_packet": not lost_path, "edit_region_in_packet": not lost_region,
                "next_action_ok": not (lost_path or lost_region)}
    if nxt["cls"] in ("test_exec", "other_bash"):
        toks = set()
        for name, inp in nxt["tools"]:
            if name == "Bash":
                toks |= {_norm(t) for t in _PATH.findall(inp.get("command") or "")}
        if not toks:
            return {"kind": nxt["cls"], "next_action_ok": True, "evidence_from_this_run": False, "note": "no path parameters"}
        lost = any((present(t, raw_text) or t in run_paths) and not (present(t, packet_text) or t in run_paths) for t in toks)
        from_run = any(present(t, raw_text) or t in run_paths for t in toks)
        return {"kind": nxt["cls"], "next_action_ok": not lost, "evidence_from_this_run": from_run}
    return None


# --------------------------------------------------------------------------- per-session analysis
def analyze_session(transcript_path, cli_meta=None):
    calls = parse_session(transcript_path)
    n = len(calls)
    validation = None
    if cli_meta:
        validation = {"calls_parsed": n, "cli_num_turns": cli_meta.get("num_turns"),
                      "match": n == cli_meta.get("num_turns")}
    runs = []
    for ri, run in enumerate(segment_runs(calls)):
        rc = run["calls"]
        transitions, breadths = [], []
        hist_text = ""
        hist_paths = set()
        seedp, seedpat, _ = targets_of(rc[0])
        for name, inp in rc[0]["tools"]:
            hist_text += json.dumps(inp)
        hist_paths |= seedp
        for k in range(1, len(rc)):
            prev = rc[k - 1]
            hist_text += "\n" + prev["out_text"]
            hist_paths |= {_norm(p) for p in _PATH.findall(prev["out_text"])}
            tc = transition_class(rc[k], prev["out_text"], hist_text, hist_paths)
            transitions.append(tc)
            cb = choice_breadth(rc[k], prev["out_text"])
            if cb:
                breadths.append(cb)
            for name, inp in rc[k]["tools"]:
                hist_text += "\n" + json.dumps(inp)
        run_class = ("D0" if transitions and all(t == "D0" for t in transitions)
                     else "D0D1" if transitions and all(t in ("D0", "D1") for t in transitions)
                     else "single" if not transitions else "D2")
        packet_text, packet_tokens, raw_tokens = build_packet(rc)
        ret = retention(run, packet_text) if run_class in ("D0", "D0D1") else None
        runs.append({
            "run_id": ri, "start_call": rc[0]["idx"], "end_call": rc[-1]["idx"], "n_calls": len(rc),
            "tools": [n0 for c in rc for n0, _ in c["tools"]],
            "tool_inputs": [json.dumps(i)[:120] for c in rc for _, i in c["tools"]][:12],
            "transitions": transitions, "run_class": run_class,
            "paths_touched": sorted({_norm(p) for c in rc for p in targets_of(c)[0]})[:20],
            "next_action": run["next"]["cls"] if run["next"] else "session_end",
            "avoided_P": sum(c["P"] for c in rc[1:]), "avoided_out_tokens": sum(c["out_tokens"] for c in rc[1:]),
            "packet_tokens": packet_tokens, "raw_out_tokens": raw_tokens,
            "retention": ret, "choice_breadth": breadths,
        })
    return {"transcript": transcript_path, "n_calls": n, "sum_P": sum(c["P"] for c in calls),
            "cls_counts": dict(Counter(c["cls"] for c in calls)), "validation": validation, "runs": runs}


# --------------------------------------------------------------------------- corpus aggregation
def _pct(a, b):
    return round(100 * a / b, 2) if b else None


def aggregate(sessions):
    tot_calls = sum(s["n_calls"] for s in sessions)
    tot_P = sum(s["sum_P"] for s in sessions)
    cls = Counter()
    for s in sessions:
        cls.update(s["cls_counts"])
    allruns = [r for s in sessions for r in s["runs"]]
    multi = [r for r in allruns if r["n_calls"] >= 2]
    lens = sorted(r["n_calls"] for r in allruns)
    def q(p):
        return lens[min(int(p * len(lens)), len(lens) - 1)] if lens else None
    def saved(runs):
        return sum(r["n_calls"] - 1 for r in runs)
    def savedP(runs):
        return sum(r["avoided_P"] for r in runs)
    d0 = [r for r in multi if r["run_class"] == "D0"]
    d01 = [r for r in multi if r["run_class"] in ("D0", "D0D1")]
    gated = [r for r in d01 if r["retention"] is None or r["retention"].get("next_action_ok")]
    gateable = [r for r in d01 if r["retention"] is not None]
    ret_ok = [r for r in gateable if r["retention"].get("next_action_ok")]
    edits = [r for r in d01 if r["retention"] and r["retention"].get("kind") == "edit"]
    breadths = [b for r in multi for b in r["choice_breadth"]]
    ranks = [b["rank"] for b in breadths if b.get("rank")]
    return {
        "corpus": {"sessions": len(sessions), "api_calls": tot_calls, "sum_P": tot_P,
                   "cli_validation_matches": sum(1 for s in sessions if (s["validation"] or {}).get("match")),
                   "cli_validation_available": sum(1 for s in sessions if s["validation"])},
        "call_classes_pct": {k: _pct(v, tot_calls) for k, v in cls.most_common()},
        "runs": {"total": len(allruns), "multi_call": len(multi),
                 "length_p50": q(0.50), "length_p90": q(0.90), "length_p95": q(0.95), "length_max": lens[-1] if lens else None,
                 "class_counts_multi": dict(Counter(r["run_class"] for r in multi)),
                 "transition_counts": dict(Counter(t for r in allruns for t in r["transitions"]))},
        "call_reduction_pct_of_all_calls": {
            "upper_bound_all_runs": _pct(saved(multi), tot_calls),
            "mechanical_D0": _pct(saved(d0), tot_calls),
            "realistic_D0D1_evidence_gated": _pct(saved(gated), tot_calls)},
        "prefix_weighted_saving_pct_of_sum_P": {
            "upper_bound_all_runs": _pct(savedP(multi), tot_P),
            "mechanical_D0": _pct(savedP(d0), tot_P),
            "realistic_D0D1_evidence_gated": _pct(savedP(gated), tot_P)},
        "evidence_retention": {
            "gateable_runs": len(gateable), "next_action_ok": len(ret_ok),
            "R_next_action": _pct(len(ret_ok), len(gateable)),
            "R_edit_target": _pct(sum(1 for r in edits if r["retention"]["edit_target_in_packet"]), len(edits)),
            "R_edit_region": _pct(sum(1 for r in edits if r["retention"]["edit_region_in_packet"]), len(edits)),
            "evidence_from_this_run_pct": _pct(sum(1 for r in gateable if r["retention"].get("evidence_from_this_run")), len(gateable)),
            "runs_not_gateable(session_end/other)": len(d01) - len(gateable),
            "note": "failure = evidence present in the run's raw outputs but LOST in the packet; evidence from prior context is neutral (collapse preserves prior context)"},
        "packet_economics": {
            "packet_tokens_total": sum(r["packet_tokens"] for r in gated),
            "raw_out_tokens_total": sum(r["raw_out_tokens"] for r in gated),
            "dedup_ratio": round(1 - (sum(r["packet_tokens"] for r in gated) / max(sum(r["raw_out_tokens"] for r in gated), 1)), 3),
            "avoided_output_tokens": sum(r["avoided_out_tokens"] for r in gated)},
        "choice_breadth": {
            "transitions_with_path_choice": len(breadths),
            "median_candidates": sorted(b["candidates"] for b in breadths)[len(breadths) // 2] if breadths else None,
            "chosen_in_top3_pct": _pct(sum(1 for r in ranks if r <= 3), len(ranks)),
            "rank_found_pct": _pct(len(ranks), len(breadths))},
    }


def run_oracle(results_json, *, arm=None):
    res = json.load(open(results_json))
    sessions, seen = [], set()
    for key, m in res.items():
        if (arm and f"|{arm}|" not in key) or not isinstance(m, dict) or "error" in m:
            continue
        tp = m.get("transcript")
        if tp and os.path.exists(tp) and tp not in seen:
            seen.add(tp)
            sessions.append(analyze_session(tp, cli_meta=m))
    return {"sessions": sessions, "aggregate": aggregate(sessions)}


def _main(argv):
    out = run_oracle(argv[1])
    a = out["aggregate"]
    print(json.dumps(a, indent=2))


if __name__ == "__main__":
    import sys
    _main(sys.argv)
