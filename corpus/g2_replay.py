#!/usr/bin/env python3
"""G2 — task-relevance graph ceiling. ZERO Claude quota. No live run.

G1's fair ablation showed the STRUCTURAL dependency graph (CALLS/IMPORTS/IMPLEMENTS) rarely links
co-EDITED symbols. But co-edit prediction is the wrong value function. The right one (this file):
given a CORRECT anchor (the edited symbols — isolating the graph question from anchor resolution),
does a cheap deterministic graph reach the SUPPORT symbols the agent actually READ (never edited) —
the context it needed to make the fix — inside a real model-visible token budget?

Layers (relations available BEFORE inference; NO future-edit leakage — future edits are ground truth
ONLY): Gstruct = CALLS+IMPORTS+IMPLEMENTS. Glocal = Gstruct + same-file + same-class + identifier
references in the anchor body. Gtask = Glocal + tests + lexical source-search + historical git
co-change. Co-change is computed STRICTLY from commits that are ancestors of the task's base commit
(`git log <base_commit> -- <anchor_file>`); the fix commit is a descendant, so there is no future-edit
leakage. Baseline `lexical` = the target symbols only (no neighborhood).

Ground truth = symbols the agent inspected (Read) over the trajectory, MINUS the edited symbols
(reported separately as edit-target recall). Local traversal/search tokens are NOT counted — they
stay outside model context; only the compiled bundle's tokens count.

Preregistered HARD gate: `R_support(Gtask) − R_support(lexical) < 0.15` at comparable-or-lower model
tokens ⇒ close graph traversal as a token-saving mechanism.
"""
from __future__ import annotations

import json
import os
import re
from collections import deque

from contextruntime.codegraph.anchors import _norm, _path_match
from corpus.g1_replay import edit_ground_truth, useful_reads_after

_STRUCT = ("CALLS", "IMPLEMENTS", "IMPORTS")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def cochange_files(mirror, base_commit, anchor_paths, topk=12, max_commits=60):
    """Files that historically CO-CHANGED with the anchor files, computed STRICTLY from commits that
    are ancestors of `base_commit` — the fix commit is a descendant, so there is NO future-edit
    leakage. Two-step because `git log --name-only -- <path>` prints only the filtered path: (1) the
    last `max_commits` commit SHAs (before the base) that touched each anchor file, (2) `git show
    --name-only` over those SHAs to union the OTHER files changed alongside. Returns the top-k
    co-changed repo-relative paths (anchor files excluded). Empty on any git error (fail-open)."""
    import subprocess
    from collections import Counter
    if not (mirror and base_commit and os.path.isdir(os.path.join(mirror, ".git"))):
        return []
    anchor_norm = {_norm(p) for p in anchor_paths if p}
    co = Counter()
    for ap in anchor_norm:
        try:
            shas = subprocess.run(["git", "-C", mirror, "log", base_commit, f"-n{max_commits}",
                                   "--pretty=format:%H", "--", ap], capture_output=True, text=True,
                                  timeout=60).stdout.split()
            if not shas:
                continue
            blocks = subprocess.run(["git", "-C", mirror, "show", "--name-only", "--pretty=format:@"]
                                    + shas, capture_output=True, text=True, timeout=120).stdout
        except Exception:      # noqa: BLE001
            continue
        for block in blocks.split("@"):
            files = {_norm(f.strip()) for f in block.splitlines() if f.strip()}
            if len(files) > 40:                          # skip sweeping refactors/renames (noise)
                continue
            for f in files - anchor_norm:
                co[f] += 1
    return [p for p, _ in co.most_common(topk)]


def needed_support(store, transcript_path, repo_id):
    """(support_symbol_ids, edited_symbol_ids): symbols the agent READ minus the ones it EDITED."""
    edited = {g["symbol_id"] for g in edit_ground_truth(transcript_path, store, repo_id) if g["symbol_id"]}
    read = useful_reads_after(transcript_path, store, repo_id)
    return (read - edited), edited


def _struct_reach(store, anchor, maxhop=2):
    seen, dq = {}, deque([(anchor, 0)])
    while dq:
        s, h = dq.popleft()
        if h >= maxhop:
            continue
        for e in store.code_edges_from(s, _STRUCT):
            n = e["dst_id"]
            if n not in seen:
                seen[n] = h + 1
                dq.append((n, h + 1))
    return seen                                          # {sid: hop}


def _row(store, sid):
    return store.symbol_row(sid)


def candidate_pool(store, anchors, layer, repo_id, cochange_paths=()):
    """{sid: rank} for symbols reachable from the anchors under `layer` (lower rank = closer). Ranks:
    struct hop1=1, hop2=2, same_class=3, ident_ref/co_change=4, same_file=5, test=6, lexical=7."""
    pool = {}
    cochange = {_norm(p) for p in cochange_paths}

    def add(sid, rank):
        if sid and sid not in anchors and (sid not in pool or rank < pool[sid]):
            pool[sid] = rank

    arows = [r for r in (_row(store, a) for a in anchors) if r]
    for a in anchors:                                    # structural (all layers)
        for sid, hop in _struct_reach(store, a).items():
            add(sid, min(hop, 2))
    if layer in ("Glocal", "Gtask"):
        paths = {_norm(r["path"]) for r in arows if r["path"]}
        classes = {r["qualified_name"].rsplit(".", 1)[0] for r in arows
                   if r["kind"] == "method" and "." in (r["qualified_name"] or "")}
        idents = set()
        for a in anchors:                                # identifiers in the anchor body
            r = _row(store, a)
            b = store.blob(r["content_hash"]) if r and r["content_hash"] else None
            if b and b["sample"]:
                idents |= set(_IDENT.findall(b["sample"]))
        for r in store.conn.execute("SELECT symbol_id, qualified_name, path, kind FROM symbols"
                                    + (" WHERE repo_id=?" if repo_id else ""),
                                    ((repo_id,) if repo_id else ())):
            qn, tail, p = r["qualified_name"] or "", (r["qualified_name"] or "").rsplit(".", 1)[-1], _norm(r["path"])
            if any(qn.startswith(c + ".") for c in classes):
                add(r["symbol_id"], 3)
            elif layer == "Gtask" and p in cochange:     # historical co-change (feature grouping)
                add(r["symbol_id"], 4)
            elif tail in idents:
                add(r["symbol_id"], 4)
            elif p in paths:
                add(r["symbol_id"], 5)
    if layer == "Gtask":
        for a in anchors:                                # tests
            for e in list(store.code_edges_from(a, ("TESTED_BY",))) + list(store.code_edges_to(a, ("TESTED_BY",))):
                add(e.get("dst_id") or e.get("src_id"), 6)
        # lexical source-search: symbols whose tail name is an identifier used by the anchors
        idents = set()
        for a in anchors:
            r = _row(store, a)
            b = store.blob(r["content_hash"]) if r and r["content_hash"] else None
            if b and b["sample"]:
                idents |= {t for t in _IDENT.findall(b["sample"]) if len(t) >= 5}
        for r in store.conn.execute("SELECT symbol_id, qualified_name FROM symbols"
                                    + (" WHERE repo_id=?" if repo_id else ""),
                                    ((repo_id,) if repo_id else ())):
            if (r["qualified_name"] or "").rsplit(".", 1)[-1] in idents:
                add(r["symbol_id"], 7)
    return pool


def _sig_tokens(store, sid):
    from contextruntime.codegraph.render import render_symbol
    r = _row(store, sid)
    if not r:
        return 0
    try:
        return render_symbol(store, r, "signature").tokens
    except Exception:      # noqa: BLE001
        return 20


def budget_recall(store, anchors, needed, layer, budget, repo_id, cochange_paths=()):
    """R_support at a model-visible token budget: targets rendered at implementation (must-keep), then
    reachable support symbols at signature by ascending rank until the budget is spent. Returns
    (R_support, model_tokens, included_support_count)."""
    from contextruntime.codegraph.render import render_symbol
    used = 0
    for a in anchors:                                    # targets (the edit bodies) are must-keep
        r = _row(store, a)
        if r:
            try:
                used += render_symbol(store, r, "implementation").tokens
            except Exception:      # noqa: BLE001
                used += 200
    if layer == "lexical":
        pool = {}
    else:
        pool = candidate_pool(store, anchors, layer, repo_id, cochange_paths)
    included = set()
    for sid, _rank in sorted(pool.items(), key=lambda kv: (kv[1], kv[0])):
        c = _sig_tokens(store, sid) + 1
        if used + c > budget:
            continue                                     # skip (try smaller later-ranked ones)
        used += c
        included.add(sid)
    hit = len(needed & included)
    return (round(hit / len(needed), 4) if needed else None, used, len(included))


def ceiling_recall(store, anchors, needed, layer, repo_id, cochange_paths=()):
    """Fraction of needed support reachable by `layer` IGNORING the token budget — the relation's
    upper bound. Separates 'is the relation informative?' (ceiling) from 'can a budget realize it?'
    (budget_recall). lexical's pool is empty ⇒ 0."""
    if layer == "lexical":
        return 0.0 if needed else None
    pool = set(candidate_pool(store, anchors, layer, repo_id, cochange_paths).keys())
    return round(len(needed & pool) / len(needed), 4) if needed else None


BUDGETS = (1024, 2048, 4096)
LAYERS = ("lexical", "Gstruct", "Glocal", "Gtask")


def _base_commit(gdb):
    try:
        return json.load(open(gdb + ".provenance.json")).get("base_commit")
    except Exception:      # noqa: BLE001
        return None


def run_g2(results_json, tasks, pilot_dir, *, arm="A_native", budgets=BUDGETS, mirror=None):
    from contextruntime.store import GraphStore
    res = json.load(open(results_json))
    rows, prov = [], {}
    for tid in tasks:
        gdb = os.path.join(pilot_dir, tid, "C_graph", "codegraph.db")
        tr = next((m["transcript"] for k, m in res.items()
                   if k.startswith(f"{tid}|{arm}|") and isinstance(m, dict) and m.get("transcript")
                   and os.path.exists(m["transcript"])), None)
        if not (tr and os.path.exists(gdb)):
            continue
        store = GraphStore(gdb)
        supp, ed = needed_support(store, tr, "django")
        anchors = ed
        base = _base_commit(gdb)
        cc = []
        if anchors and mirror and base:
            apaths = {r["path"] for r in (_row(store, a) for a in anchors) if r and r["path"]}
            cc = cochange_files(mirror, base, apaths)
        ceil = {}
        if anchors and supp:
            for layer in LAYERS:
                ceil[layer] = ceiling_recall(store, anchors, supp, layer, "django", cc)
            for B in budgets:
                for layer in LAYERS:
                    R, toks, inc = budget_recall(store, anchors, supp, layer, B, "django", cc)
                    rows.append({"task": tid, "budget": B, "layer": layer, "n_support": len(supp),
                                 "R_support": R, "model_tokens": toks, "included_support": inc,
                                 "ceiling": ceil[layer]})
        prov[tid] = {"n_support_symbols": len(supp), "n_edited": len(ed),
                     "graph": os.path.basename(gdb), "base_commit": base,
                     "cochange_files": len(cc), "ceiling": ceil}
        store.close()
    return {"rows": rows, "aggregate": _agg(rows, budgets), "provenance": prov,
            "gate": "R_support(Gtask) - R_support(lexical) >= 0.15 at <= tokens, else CLOSE graph"}


def _agg(rows, budgets):
    def _m(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 4) if xs else None
    out = {}
    for B in budgets:
        row = {}
        for layer in LAYERS:
            sel = [r for r in rows if r["budget"] == B and r["layer"] == layer]
            row[layer] = {"R_support": _m([r["R_support"] for r in sel]),
                          "model_tokens": _m([r["model_tokens"] for r in sel]),
                          "ceiling": _m([r["ceiling"] for r in sel]), "n": len(sel)}
        base = row["lexical"]["R_support"] or 0.0
        row["Gtask_minus_lexical"] = round((row["Gtask"]["R_support"] or 0) - base, 4)
        row["Glocal_minus_lexical"] = round((row["Glocal"]["R_support"] or 0) - base, 4)
        row["Gtask_ceiling_minus_Glocal"] = round((row["Gtask"]["ceiling"] or 0) - (row["Glocal"]["ceiling"] or 0), 4)
        out[f"@{B}"] = row
    return out


def _main(argv):
    from corpus.step7_live_experiment import TASKS
    out = run_g2(argv[1], TASKS, argv[2])
    for B, row in out["aggregate"].items():
        print(f"budget {B}: " + "  ".join(
            f"{L}={row[L]['R_support']}({row[L]['model_tokens']}tok)" for L in LAYERS)
            + f"  | Gtask-lex={row['Gtask_minus_lexical']} Glocal-lex={row['Glocal_minus_lexical']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
