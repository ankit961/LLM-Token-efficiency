#!/usr/bin/env python3
"""G1 — offline Graph-First Context Compilation replay. ZERO Claude quota.

The scientific question: can deterministic anchor resolution + graph-constrained source compilation
deliver the exact code an agent later needs, at substantially lower model-visible token cost than
native exploratory Read/Grep — independent of voluntary tool adoption?

Method, per task (native A_native Step-7 trajectory + the task's code graph):
  GROUND TRUTH (held out): the symbols the agent EDITED (symbol_at on each edit's line) and their
    exact edit regions (old_string); plus the files/symbols it subsequently READ.
  ANCHORS (no future leakage): generated ONLY from the problem statement — traceback frames,
    file:line refs, file refs, free-text identifiers. The edit location is NEVER used to anchor.
  Then compile a bundle per anchor × budget × ablation and score recall + tokens.

Ablations: A native (baseline tokens), B lexical-only (target impl, no graph neighborhood),
C graph-first (target + graph neighborhood), D generic skeleton (B2-style reduce_file on the file).
No live run; token "savings" are labeled counterfactual opportunities, not realized savings.
"""
from __future__ import annotations

import json
import os
import re

from contextruntime.codegraph.anchors import symbol_at, traceback_anchors
from contextruntime.reducers.base import tokens as _tok
from contextruntime.reducers.library import _LINENO_PREFIX
from corpus.edit_recall_replay import parse_reads_edits

_FILE_LINE = re.compile(r'([\w./-]+\.py):(\d+)')                 # path.py:123 references in prose


def _lineno_of(content: str, needle_first_line: str):
    """The 1-based source line of `needle_first_line` within a Read output that carries '  123→'
    line-number prefixes. None if the read has no line numbers or the line isn't found."""
    target = needle_first_line.rstrip()
    for ln in (content or "").splitlines():
        m = _LINENO_PREFIX.match(ln)
        if m and _LINENO_PREFIX.sub("", ln, count=1).rstrip() == target:
            try:
                return int(re.match(r"\s*(\d+)", ln).group(1))
            except (AttributeError, ValueError):
                return None
    return None


def edit_ground_truth(transcript_path: str, store, repo_id=None) -> list:
    """[{path, line, old_string, symbol_id, qualified_name}] for edits whose target symbol resolves
    via symbol_at on the edit line (recovered from the most recent prior read of that file)."""
    reads, edits = parse_reads_edits(transcript_path)
    out = []
    for (te, path, old) in edits:
        first = next((l for l in old.splitlines() if l.strip()), "")
        prior = [c for (tr, p, c) in reads if p == path and tr <= te]
        line = next((L for c in reversed(prior) if (L := _lineno_of(c, first)) is not None), None)
        sym = symbol_at(store, path, line, repo_id) if line is not None else None
        out.append({"path": path, "line": line, "old_string": old,
                    "symbol_id": sym["symbol_id"] if sym else None,
                    "qualified_name": sym["qualified_name"] if sym else None})
    return out


def available_anchors(problem_statement: str) -> dict:
    """Anchor signals present in the problem statement — no future leakage. traceback frames,
    file:line references, and the raw text (for free-text resolution)."""
    tb = traceback_anchors(problem_statement)
    fl = [(m.group(1), int(m.group(2))) for m in _FILE_LINE.finditer(problem_statement or "")]
    return {"traceback_frames": tb, "file_line_refs": fl,
            "has_traceback": bool(tb), "has_file_line": bool(fl),
            "problem_tokens": _tok(problem_statement or "")}


def load_problem_statement(spec_path: str) -> str:
    try:
        from contextruntime.corpusrunner import parse_spec
        return parse_spec(spec_path).problem_statement
    except Exception:      # noqa: BLE001
        return ""


# ------------------------------------------------------------------ bundles (ablations) + metrics
_TOUCH_TOOLS = {"Read", "NotebookRead"}


def _bundle_symbols(read_result) -> set:
    return {s["symbol_id"] for s in read_result.sections} if read_result else set()


def compile_bundle(store, root_id: str, *, budget: int, mode: str, repo_id=None,
                   file_content: str = ""):
    """One ablation bundle for a resolved root. Returns (text, tokens, symbol_ids-present).
      B lexical: target implementation only, no graph neighborhood (read_symbol, deps off).
      C graph  : target + graph dependency neighborhood (read_symbol, deps on).
      D skeleton: B2-style reduce_file on the WHOLE edited file (signatures, bodies dropped)."""
    from contextruntime.semanticfs import read_symbol
    if mode == "D":
        from contextruntime.reducers.library import reduce_file
        red = reduce_file(file_content or "", {}, budget_tokens=budget)
        return red.reduced_text, red.reduced_tokens, set()
    rr = read_symbol(store, root_id, budget=budget, repo_id=repo_id,
                     include_dependencies=(mode == "C"))
    return rr.to_text(), rr.budget.get("serialized_tokens", _tok(rr.to_text())), _bundle_symbols(rr)


def edit_line_coverage(bundle_text: str, old_string: str) -> float:
    """Fraction of the edit region's non-blank lines present in the bundle, matched by LSTRIPPED
    content (so an indentation difference between the stored blob and the agent's old_string is not a
    spurious miss). This is edit-LINE/body recall — never path-name recall."""
    btext = {_LINENO_PREFIX.sub("", l, count=1).strip() for l in bundle_text.splitlines()}
    want = [l.strip() for l in old_string.splitlines() if l.strip()]
    if not want:
        return 0.0
    return sum(1 for w in want if w in btext) / len(want)


def _edit_line_present(bundle_text: str, old_string: str, thresh: float = 0.9) -> bool:
    """Edit-line recall as a boolean: >= `thresh` of the region's lines present (default 0.9, so a
    single whitespace-only mismatch on the signature line does not fail an otherwise-complete body)."""
    return edit_line_coverage(bundle_text, old_string) >= thresh


def _read_line_span(content: str):
    nums = [int(re.match(r"\s*(\d+)", l).group(1)) for l in content.splitlines()
            if _LINENO_PREFIX.match(l)]
    return (min(nums), max(nums)) if nums else None


def useful_reads_after(transcript_path: str, store, repo_id=None) -> set:
    """The realized useful-context set: EVERY symbol whose span overlaps a file region the agent Read
    (not just the first line) — the symbols a good initial bundle should already hold so the agent
    need not read them. Overlap is computed per read's [min,max] line window against symbol spans."""
    reads, _ = parse_reads_edits(transcript_path)
    out = set()
    for (_t, path, content) in reads:
        span = _read_line_span(content)
        if not span:
            continue
        lo, hi = span
        q = ("SELECT symbol_id, path, start_line, end_line FROM symbols "
             "WHERE start_line IS NOT NULL AND end_line IS NOT NULL AND start_line <= ? AND end_line >= ?")
        args = [hi, lo]
        if repo_id:
            q += " AND repo_id=?"; args.append(repo_id)
        from contextruntime.codegraph.anchors import _path_match
        for r in store.conn.execute(q, args):
            if _path_match(path, r["path"]):
                out.add(r["symbol_id"])
    return out


def native_exploration_tokens(transcript_path: str, edited_path: str) -> int:
    """T_N — native tokens spent Reading the edited file (the material the agent read to examine the
    region a graph bundle would compile). A conservative proxy: the file's read outputs."""
    reads, _ = parse_reads_edits(transcript_path)
    return sum(_tok(c) for (_t, p, c) in reads if p == edited_path)


BUDGETS = (512, 1024, 2048, 4096)


def score_task(store, transcript_path: str, problem_statement: str, *, repo_id=None,
               budgets=BUDGETS) -> list:
    """Per-edit rows. Two anchor regimes: `oracle` (root = the edited symbol — isolates the COMPILER,
    an upper bound, NOT leakage-free) and `problem` (root resolved from the problem statement only —
    the no-leakage PIPELINE). Ablations B/C/D per budget. Reads/edits recovered from the transcript."""
    from contextruntime.codegraph.compile import resolve_anchor
    gt = edit_ground_truth(transcript_path, store, repo_id)
    scorable = [g for g in gt if g["symbol_id"]]
    useful = useful_reads_after(transcript_path, store, repo_id)
    reads, _ = parse_reads_edits(transcript_path)
    file_content = {}
    for (_t, p, c) in reads:
        file_content[p] = c                                     # last read of each file (for skeleton D)
    prob_root, prob_kind = resolve_anchor(store, query=problem_statement,
                                          traceback=problem_statement, repo_id=repo_id)
    rows = []
    for g in scorable:
        edited = g["symbol_id"]
        erow = store.symbol_row(edited)
        root_kind = erow["kind"] if erow else None                # module edits (imports) are a known gap
        t_n = native_exploration_tokens(transcript_path, g["path"])
        for B in budgets:
            for regime, root in (("oracle", edited),
                                 ("problem", prob_root["symbol_id"] if prob_root else None)):
                if root is None:
                    rows.append({"edit_symbol": g["qualified_name"], "regime": regime, "budget": B,
                                 "anchor_kind": prob_kind, "resolved": False})
                    continue
                rec = {}
                for mode in ("B", "C", "D"):
                    text, tg, syms = compile_bundle(store, root, budget=B, mode=mode, repo_id=repo_id,
                                                    file_content=file_content.get(g["path"], ""))
                    esr = (edited in syms) or (mode == "D" and (g["qualified_name"] or "").rsplit(".", 1)[-1] in text)
                    rec[mode] = {"edit_symbol_recall": bool(esr),
                                 "edit_line_recall": _edit_line_present(text, g["old_string"]),
                                 "edit_line_coverage": round(edit_line_coverage(text, g["old_string"]), 4),
                                 "useful_symbol_recall": round(len(syms & useful) / len(useful), 4) if useful else None,
                                 "bundle_tokens": tg}
                rows.append({
                    "edit_symbol": g["qualified_name"], "root_kind": root_kind,
                    "regime": regime, "budget": B,
                    "anchor_kind": prob_kind if regime == "problem" else "oracle_edited_symbol",
                    "resolved": True, "root_is_edited": (root == edited),
                    "native_exploration_tokens": t_n,
                    "B_lexical": rec["B"], "C_graph": rec["C"], "D_skeleton": rec["D"],
                    "graph_increment_over_lexical": round(
                        (rec["C"]["useful_symbol_recall"] or 0) - (rec["B"]["useful_symbol_recall"] or 0), 4),
                })
    return rows


def _graph_provenance(gdb: str) -> dict:
    try:
        p = json.load(open(gdb + ".provenance.json"))
        return {"base_commit": p.get("base_commit"), "graph_db_sha256": p.get("graph_db_sha256"),
                "files_indexed": p.get("files_indexed")}
    except Exception:      # noqa: BLE001
        return {}


def run_g1(results_json: str, tasks: dict, pilot_dir: str, *, arm="A_native", budgets=BUDGETS) -> dict:
    """Full offline G1 over the native trajectories. Returns per-edit rows + aggregate + provenance."""
    from contextruntime.store import GraphStore
    res = json.load(open(results_json))
    all_rows, prov = [], {}
    for tid, spec in tasks.items():
        gdb = os.path.join(pilot_dir, tid, "C_graph", "codegraph.db")
        tr = next((m["transcript"] for k, m in res.items()
                   if k.startswith(f"{tid}|{arm}|") and isinstance(m, dict) and m.get("transcript")
                   and os.path.exists(m["transcript"])), None)
        if not (tr and os.path.exists(gdb)):
            continue
        store = GraphStore(gdb)
        for r in score_task(store, tr, load_problem_statement(spec), repo_id="django", budgets=budgets):
            r["task"] = tid
            all_rows.append(r)
        store.close()
        prov[tid] = {"graph_db": os.path.basename(gdb), "transcript": os.path.basename(tr),
                     "spec": spec, **_graph_provenance(gdb)}
    return {"rows": all_rows, "aggregate": _aggregate(all_rows, budgets), "n_rows": len(all_rows),
            "provenance": {"tasks": prov, "arm": arm, "budgets": list(budgets), "repo_id": "django",
                           "task_set": sorted(tasks)}}


def _aggregate(rows, budgets) -> dict:
    def _m(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 4) if xs else None
    out = {}
    for regime in ("oracle", "problem"):
        for B in budgets:
            sel = [r for r in rows if r["regime"] == regime and r["budget"] == B and r.get("resolved")]
            if not sel:
                out[f"{regime}@{B}"] = {"n": 0, "anchor_resolved_frac": round(
                    sum(1 for r in rows if r["regime"] == regime and r["budget"] == B and r.get("resolved"))
                    / max(1, sum(1 for r in rows if r["regime"] == regime and r["budget"] == B)), 4)}
                continue
            tg = _m([r["C_graph"]["bundle_tokens"] for r in sel])
            tn = _m([r["native_exploration_tokens"] for r in sel])
            fn = [r for r in sel if r.get("root_kind") in ("function", "method")]   # exclude module-import edits
            out[f"{regime}@{B}"] = {
                "n": len(sel), "n_function_edits": len(fn),
                "root_is_edited_frac": _m([1.0 if r["root_is_edited"] else 0.0 for r in sel]),
                "edit_symbol_recall_C": _m([1.0 if r["C_graph"]["edit_symbol_recall"] else 0.0 for r in sel]),
                # edit-LINE/body recall: boolean (>=0.9 coverage) and mean coverage, C vs B vs D
                "edit_line_recall_C": _m([1.0 if r["C_graph"]["edit_line_recall"] else 0.0 for r in sel]),
                "edit_line_coverage_C": _m([r["C_graph"]["edit_line_coverage"] for r in sel]),
                "edit_line_recall_C_function_only": _m([1.0 if r["C_graph"]["edit_line_recall"] else 0.0 for r in fn]),
                "edit_line_coverage_C_function_only": _m([r["C_graph"]["edit_line_coverage"] for r in fn]),
                "edit_line_recall_B": _m([1.0 if r["B_lexical"]["edit_line_recall"] else 0.0 for r in sel]),
                "edit_line_recall_D_skeleton": _m([1.0 if r["D_skeleton"]["edit_line_recall"] else 0.0 for r in sel]),
                "edit_line_coverage_D_skeleton": _m([r["D_skeleton"]["edit_line_coverage"] for r in sel]),
                "useful_recall_B": _m([r["B_lexical"]["useful_symbol_recall"] for r in sel]),
                "useful_recall_C": _m([r["C_graph"]["useful_symbol_recall"] for r in sel]),
                "graph_increment_over_lexical": _m([r["graph_increment_over_lexical"] for r in sel]),
                "bundle_tokens_C": tg, "native_exploration_tokens": tn,
                "token_compression_C": round(1 - tg / tn, 4) if (tg and tn) else None,
            }
    return out


def _main(argv) -> None:
    """Smoke: python -m corpus.g1_replay <graph_db> <transcript> [spec]"""
    from contextruntime.store import GraphStore
    store = GraphStore(argv[1])
    gt = edit_ground_truth(argv[2], store, repo_id="django")
    resolved = [g for g in gt if g["symbol_id"]]
    print(f"edits: {len(gt)}  edit-symbols resolved via symbol_at: {len(resolved)}")
    for g in resolved[:5]:
        print(f"  {g['path']}:{g['line']} -> {g['qualified_name']}")
    if len(argv) > 3:
        a = available_anchors(load_problem_statement(argv[3]))
        print(f"anchors in problem: traceback={a['has_traceback']} file_line={a['has_file_line']} "
              f"problem_tokens={a['problem_tokens']}")
    store.close()


if __name__ == "__main__":
    import sys
    _main(sys.argv)
