#!/usr/bin/env python3
"""Step 6.1 — line-level evidence-retention replay: does GRAPH ranking preserve more useful MATCH
LINES (not just path NAMES) than simple reduction, at equal budget? ZERO Claude quota.

Step 6's `path_recall` saturates at 100% and therefore cannot separate simple from graph, because
when the reducer truncates it emits a per-file rollup that NAMES up to 12 matched files even when
their actual match lines were dropped. `named(path) != useful line retained`. Graph ranking changes
WHICH match lines survive the budget — precisely the axis name-recall is blind to. This module runs
both reducers on the identical raw output, with a REAL per-task code graph and a working set built
only from PRECEDING trajectory events, and compares:

  * Recall_line — fraction of subsequently-needed paths that keep >=1 real MATCH LINE (not rollup);
  * ExpansionPressure — # needed paths left NAMED-ONLY (rollup, no line) ⇒ a forced result:// expand;
  * promoted / demoted — needed paths whose line graph kept but simple dropped, and vice-versa;
  * RankGain — how much earlier graph orders a needed path's first kept line.

Faithfulness: reuses gr's constants, gr._path_matches, gr.path_scores' collapse, gr._kept_match_lines
and the REAL reduce_search. Only `_proximity` is reimplemented in-memory (identical best-first walk)
because the sqlite-per-node walk is too slow on django's dense graph (a degree-6857 hub) for an
offline sweep. `_InMemoryProximity` is unit-tested to match gr._proximity exactly (tests via a fake
store over the same edges). See tests/test_graph_evidence_replay.py.
"""
from __future__ import annotations

import glob
import heapq
import json
import os
import re
from collections import defaultdict

from contextruntime.reducers import graphrank as gr
from contextruntime.reducers.base import tokens as _tok
from contextruntime.reducers.gate import route
from contextruntime.reducers.library import (_match_path, reduce_search, search_matched_paths)
from contextruntime.store import GraphStore
from corpus.paired_replay import _needed_paths, _retained_paths, future_paths, parse_transcript
from corpus.reduction_replay import measured_reduction

_TASK_RE = re.compile(r"django[-_]+(\d{4,6})")


# ------------------------------------------------------------------ faithful in-memory proximity
def _proximity_inmemory(adj, anchors) -> dict:
    """Byte-for-byte the same best-first relaxation as gr._proximity, over an in-memory adjacency
    `adj[sid] -> list[(neighbor, edge_type, confidence)]` (both directions, as gr._edges_both
    yields). Reuses gr's MAX_DEPTH / DISTANCE_DECAY / RELATION_WEIGHT so it cannot drift."""
    best = {a: 1.0 for a in anchors}
    heap = [(-1.0, a) for a in anchors]
    heapq.heapify(heap)
    depth_of = {a: 0 for a in anchors}
    settled: set = set()
    while heap:
        neg, sid = heapq.heappop(heap)
        if sid in settled:
            continue
        settled.add(sid)
        depth = depth_of[sid]
        if depth >= gr.MAX_DEPTH:
            continue
        score = -neg
        for nb, etype, conf in adj.get(sid, ()):
            if nb in settled:
                continue
            cand = score * gr.DISTANCE_DECAY * gr.RELATION_WEIGHT.get(etype, 0.5) * (conf or 0.0)
            if cand > best.get(nb, 0.0):
                best[nb] = cand
                depth_of[nb] = depth + 1
                heapq.heappush(heap, (-cand, nb))
    return best


class TaskGraph:
    """A per-task code graph loaded ONCE into memory (symbols + working-set edges), exposing the
    same path_scores(matched, ws) the live reducer computes — but fast enough to sweep offline."""

    def __init__(self, db: str, repo_id: str = "django"):
        self.store = GraphStore(db)
        self.repo_id = repo_id
        self.syms = [(r["symbol_id"], gr._norm(r["path"])) for r in self.store.symbols(repo_id)]
        self.adj = defaultdict(list)
        q = ("SELECT src_id,dst_id,edge_type,confidence FROM code_edges WHERE repo_id=? "
             "AND edge_type IN (%s)" % ",".join("?" * len(gr.WORKING_SET_EDGES)))
        for src, dst, etype, conf in self.store.conn.execute(q, (repo_id, *gr.WORKING_SET_EDGES)):
            self.adj[src].append((dst, etype, conf))       # matches gr._edges_both: from-edges → dst
            self.adj[dst].append((src, etype, conf))       #                          to-edges   → src

    def _anchors(self, ws) -> set:
        anchors = set(ws.mentioned_symbols)
        if ws.touched_paths:
            for sid, path in self.syms:                    # replicates gr._anchor_symbols
                if gr._path_matches(path, ws.touched_paths):
                    anchors.add(sid)
        return anchors

    def path_scores(self, matched_paths, ws) -> dict:
        """Faithful copy of gr.path_scores' collapse, over the in-memory proximity."""
        matched = [m for m in matched_paths if m]
        if ws.empty or not matched:
            return {}
        anchors = self._anchors(ws)
        if not anchors:
            return {}
        prox = _proximity_inmemory(self.adj, anchors)
        by_file: dict = {}
        for sid, p in self.syms:                           # collapse symbol scores onto their file
            s = prox.get(sid)
            if s and s > by_file.get(p, 0.0):
                by_file[p] = s
        if not by_file:
            return {}
        out: dict = {}
        for mp in matched:
            nmp = gr._norm(mp)
            sc = by_file.get(nmp)
            if sc is None:                                 # forgiving alignment at component boundaries
                for p, s in by_file.items():
                    if gr._suffix_match(p, nmp):
                        sc = max(sc or 0.0, s)
            if sc:
                out[mp] = sc
        return out

    def close(self):
        self.store.close()


# ------------------------------------------------------------------ per-event line-retention record
def _first_line_rank(kept_lines, needed_path) -> int:
    """Index of the first kept MATCH LINE belonging to `needed_path` (component-aware), or a large
    sentinel if none — so a smaller rank = named earlier in the kept set."""
    for i, ln in enumerate(kept_lines):
        if gr._suffix_match(_match_path(ln), needed_path):
            return i
    return 10 ** 6


def _has_line(kept_lines, needed_path) -> bool:
    return any(gr._suffix_match(_match_path(ln), needed_path) for ln in kept_lines)


def compare_event(tg, ev, events, *, budget: int, floor: int, k: int = 8):
    """For one reducible search event that FIRES at (budget,floor): run simple vs graph reduction on
    its real raw output. Returns (event_stats, per_path_records) or None when the event didn't fire
    or has no future-needed matched path. `event_stats` characterizes whether graph could act at all
    (kept-line counts, any reordering); per-path records carry name/line retention under each arm."""
    raw = ev.raw_output
    if not raw:
        return None
    red_tok, eligible = measured_reduction(raw, ev.tool, ev.tool_input, budget=budget, floor=floor)
    if not (eligible and red_tok < _tok(raw)):
        return None                                        # not reduced ⇒ nothing dropped
    needed = _needed_paths(search_matched_paths(raw), future_paths(events, ev, k))
    if not needed:
        return None
    touched = frozenset(e.path for e in events if e.kind == "touch" and e.seq < ev.seq and e.path)
    ws = gr.WorkingSet(touched, frozenset())               # live hook uses touched-only working set
    scores = tg.path_scores(search_matched_paths(raw), ws)
    rep = route(ev.tool, ev.tool_input).representation or "search"
    simple = reduce_search(raw, {}, budget_tokens=budget, representation=rep)
    graph = reduce_search(raw, {}, budget_tokens=budget, representation=rep, path_scores=scores or None)
    kept_s = gr._kept_match_lines(raw, simple)
    kept_g = gr._kept_match_lines(raw, graph)
    set_s, set_g = set(kept_s), set(kept_g)
    stats = {
        "graph_active": bool(scores),
        "kept_lines_simple": len(kept_s), "kept_lines_graph": len(kept_g),
        "promoted_any": len([l for l in kept_g if l not in set_s]),   # lines graph kept, simple dropped
        "demoted_any": len([l for l in kept_s if l not in set_g]),
    }
    recs = [{
        "path": p,
        "named_simple": bool(_retained_paths(simple.reduced_text, {p})),
        "named_graph": bool(_retained_paths(graph.reduced_text, {p})),
        "line_simple": _has_line(kept_s, p),
        "line_graph": _has_line(kept_g, p),
        "rank_simple": _first_line_rank(kept_s, p),
        "rank_graph": _first_line_rank(kept_g, p),
    } for p in needed]
    return stats, recs


# ------------------------------------------------------------------ aggregation over the corpus
def line_retention_analysis(task_transcripts, task_graphs, *, budget: int, floor: int, k: int = 8) -> dict:
    """Aggregate the line-level comparison over every reducible-and-firing event across tasks.
    `task_transcripts`: {task_id: [transcript_path,...]}; `task_graphs`: {task_id: TaskGraph}."""
    needed = named_s = named_g = line_s = line_g = 0
    promoted = demoted = graph_active_events = 0
    kept_s_tot = kept_g_tot = promoted_any = 0
    rank_gain_sum = rank_gain_n = 0
    for task_id, tpaths in task_transcripts.items():
        tg = task_graphs.get(task_id)
        if tg is None:
            continue
        for tp in tpaths:
            events = parse_transcript(tp)
            se = [e for e in events if e.kind == "search" and e.raw_output]
            for ev in se:
                out = compare_event(tg, ev, events, budget=budget, floor=floor, k=k)
                if out is None:
                    continue
                stats, recs = out
                kept_s_tot += stats["kept_lines_simple"]; kept_g_tot += stats["kept_lines_graph"]
                if stats["graph_active"]:
                    graph_active_events += 1
                    promoted_any += stats["promoted_any"]
                for r in recs:
                    needed += 1
                    named_s += r["named_simple"]; named_g += r["named_graph"]
                    line_s += r["line_simple"]; line_g += r["line_graph"]
                    promoted += 1 if (r["line_graph"] and not r["line_simple"]) else 0
                    demoted += 1 if (r["line_simple"] and not r["line_graph"]) else 0
                    if r["line_simple"] and r["line_graph"]:
                        rank_gain_sum += r["rank_simple"] - r["rank_graph"]; rank_gain_n += 1
    return {
        "budget": budget, "floor": floor, "needed_paths": needed,
        "graph_active_events": graph_active_events,
        "kept_lines_simple": kept_s_tot, "kept_lines_graph": kept_g_tot,
        "graph_reordered_any_lines": promoted_any,         # realized treatment intensity: how many
        #   kept lines graph actually moved vs simple. Near-0 ⇒ graph ranking is ~indistinguishable
        #   from simple order on these traces (low treatment intensity), NOT a high-powered negative.
        "name_recall_simple": round(named_s / needed, 4) if needed else None,
        "name_recall_graph": round(named_g / needed, 4) if needed else None,
        "line_recall_simple": round(line_s / needed, 4) if needed else None,
        "line_recall_graph": round(line_g / needed, 4) if needed else None,
        # named but NO inline match line. NOT a measured recovery cost: the omitted text is one of
        # several routes away (native Read, re-search, exact result:// expand) IF it were needed at
        # all — and since `needed` paths are ones the trajectory opened anyway, many incur no extra op.
        "inline_evidence_deficit_simple": named_s - line_s,
        "inline_evidence_deficit_graph": named_g - line_g,
        "graph_promoted_needed_lines": promoted,           # needed line graph kept that simple dropped
        "graph_demoted_needed_lines": demoted,             # needed line simple kept that graph dropped
        "mean_rank_gain": round(rank_gain_sum / rank_gain_n, 3) if rank_gain_n else None,
    }


# ------------------------------------------------------------------ wiring
def map_transcripts_to_tasks(transcript_glob: str) -> dict:
    out = defaultdict(list)
    for p in sorted(glob.glob(transcript_glob)):
        m = _TASK_RE.search(p)
        if m:
            out[m.group(1)].append(p)
    return dict(out)


def _expected_base_commit(pilot_dir: str, tid: str):
    """A task's base_commit from a run MANIFEST (the RunSpec that drove the pilot) — an independent
    source of truth, NOT the graph's own provenance file, so the check below can't be self-fulfilling."""
    for arm in ("A_native", "B_shipped", "B_tuned", "C_graph"):
        mf = os.path.join(pilot_dir, f"django__django-{tid}", arm, "manifest.json")
        if os.path.exists(mf):
            bc = json.load(open(mf)).get("base_commit")
            if bc:
                return bc
    return None


def load_task_graphs(pilot_dir: str, task_ids, repo_id: str = "django", *, verify: bool = True) -> dict:
    """Open each task's prebuilt C-arm code graph. Evidence-grade: by default REFUSE to rank against
    a graph whose provenance does not match the task's independently-recorded base_commit AND whose
    stored DB hash does not match the file — a fail-loud precondition, not directory-name trust."""
    from corpus.step5_experiment import verify_graph_provenance
    graphs = {}
    for tid in task_ids:
        db = os.path.join(pilot_dir, f"django__django-{tid}", "C_graph", "codegraph.db")
        if not os.path.exists(db):
            continue
        if verify:
            expected = _expected_base_commit(pilot_dir, tid)
            if not expected:
                raise RuntimeError(f"task {tid}: no independent base_commit (manifest) to verify the graph")
            if not verify_graph_provenance(db, expected):
                raise RuntimeError(
                    f"task {tid}: graph provenance FAILED — provenance base_commit / DB-hash do not "
                    f"match the manifest base_commit {expected[:12]}; refusing to rank against it")
        graphs[tid] = TaskGraph(db, repo_id)
    return graphs


CONFIGS = [(256, 400), (256, 244), (256, 125), (64, 244), (64, 125)]


def _main(argv) -> None:
    """python -m corpus.graph_evidence_replay '<transcript-glob>' <pilot_dir>"""
    tmap = map_transcripts_to_tasks(argv[1])
    graphs = load_task_graphs(argv[2], tmap.keys())
    print(f"tasks={list(tmap)}  graphs_loaded={list(graphs)}")
    for budget, floor in CONFIGS:
        r = line_retention_analysis(tmap, graphs, budget=budget, floor=floor)
        print(f"\n(b={budget}, f={floor})  needed={r['needed_paths']}  "
              f"graph_active_events={r['graph_active_events']}  "
              f"kept_lines S/G={r['kept_lines_simple']}/{r['kept_lines_graph']}  "
              f"graph_reordered_any={r['graph_reordered_any_lines']}")
        print(f"  name_recall  simple={r['name_recall_simple']}  graph={r['name_recall_graph']}")
        print(f"  LINE_recall  simple={r['line_recall_simple']}  graph={r['line_recall_graph']}")
        print(f"  inline_evidence_deficit  simple={r['inline_evidence_deficit_simple']}"
              f"  graph={r['inline_evidence_deficit_graph']}")
        print(f"  graph promoted_needed={r['graph_promoted_needed_lines']}  "
              f"demoted_needed={r['graph_demoted_needed_lines']}  mean_rank_gain={r['mean_rank_gain']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
