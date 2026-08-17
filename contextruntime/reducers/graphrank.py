"""B1.2 — graph-informed ranking for search/listing compaction (Transparent Reduction
Contract v0.1 §3.1–3.2).

When a search's matched paths overlap indexed symbols in the Code Graph, rank the compact
summary by *graph relevance to the task working set* instead of raw file order. This is an
APPLICATION of the existing bundle planner's utility idea (confidence × distance-decay,
`codegraph/bundle.py`), not a new optimizer — the same structural signals, aimed at a
working-set query.

Working set — v0, deliberately minimal (contract §3.2), two anchor sources only:
  * TOUCHED — paths this session already read/edited (live: from the HookJournal). A match in
    a file you're already working in is more relevant than one in a file you've never opened.
  * MENTIONS — symbols whose (qualified or short) name appears in the task prompt. PRIVACY-SAFE
    by construction: only symbol_ids that actually RESOLVE in the graph are kept; raw prompt
    tokens are never stored or emitted. (Not derivable live yet — the journal is metadata-only
    and does not store prompt text — so the live hook uses TOUCHED-only anchors; this core is
    ready for prompt text whenever a capture path exists.)

Everything here is pure and FAIL-OPEN: no anchors, no graph, or any error → empty scores,
which the reducer treats as "rank by file order" (i.e. identical to the B1.1 simple reducer).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from ..store import GraphStore

# Edge types that carry working-set relevance, with a weight per type (mirrors the bundle
# planner's relation_weight; TESTED_BY/DEPENDS_ON added for the working-set query). Traversed
# in BOTH directions — a match that CALLS into a touched file and one CALLED BY it are both
# relevant, as is a file TESTED_BY a touched test.
WORKING_SET_EDGES = ("CALLS", "DEPENDS_ON", "TESTED_BY", "IMPORTS", "IMPLEMENTS")
RELATION_WEIGHT = {"IMPLEMENTS": 1.0, "TESTED_BY": 0.9, "DEPENDS_ON": 0.85,
                   "CALLS": 0.85, "IMPORTS": 0.5}
DISTANCE_DECAY = 0.6        # score decays by this factor per hop (bundle planner's default)
MAX_DEPTH = 3              # bounded BFS — proximity beyond a few hops is noise
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")     # ≥3-char identifiers from prompt text


@dataclass(frozen=True)
class WorkingSet:
    touched_paths: frozenset       # normalized repo-relative paths read/edited this session
    mentioned_symbols: frozenset   # symbol_ids resolved from the prompt (never raw tokens)

    @property
    def empty(self) -> bool:
        return not self.touched_paths and not self.mentioned_symbols


def _norm(p: str) -> str:
    """Normalize a path for cross-source matching (grep output vs symbol.path vs journal)."""
    p = (p or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _path_matches(symbol_path: str, touched: frozenset) -> bool:
    sp = _norm(symbol_path)
    if sp in touched:
        return True
    # forgiving suffix match — an absolute journal path vs a repo-relative symbol path
    return any(sp.endswith(t) or t.endswith(sp) for t in touched if t and sp)


def build_working_set(store: GraphStore, repo_id: str, *,
                      touched_paths: Iterable[str] = (), prompt_text: str = "",
                      max_mentions: int = 24) -> WorkingSet:
    """Assemble the anchor set. MENTIONS resolution is CONSERVATIVE — a prompt token becomes
    an anchor only when it maps to a confident symbol (exact qualified name, or a unique
    short name); an ambiguous short name (many symbols) is dropped, never mass-anchored."""
    touched = frozenset(_norm(p) for p in touched_paths if p)
    mentioned: set = set()
    if prompt_text:
        for tok in sorted(set(_IDENT.findall(prompt_text))):
            if len(mentioned) >= max_mentions:
                break
            rows = store.search_symbols(tok, repo_id, limit=3)
            exact = [r for r in rows if r["qualified_name"] == tok
                     or r["qualified_name"].rsplit(".", 1)[-1] == tok]
            if len(exact) == 1:                      # unique, confident resolution only
                mentioned.add(exact[0]["symbol_id"])
    return WorkingSet(touched, frozenset(mentioned))


def _edges_both(store: GraphStore, sid: str):
    """(neighbor_id, edge_type, confidence) over working-set edges, both directions."""
    for e in store.code_edges_from(sid, WORKING_SET_EDGES):
        yield e["dst_id"], e["edge_type"], e["confidence"]
    for e in store.code_edges_to(sid, WORKING_SET_EDGES):
        yield e["src_id"], e["edge_type"], e["confidence"]


def _anchor_symbols(store: GraphStore, repo_id: str, ws: WorkingSet) -> set:
    anchors = set(ws.mentioned_symbols)
    if ws.touched_paths:
        for row in store.symbols(repo_id):
            if _path_matches(row["path"], ws.touched_paths):
                anchors.add(row["symbol_id"])
    return anchors


def _proximity(store: GraphStore, anchors: set) -> dict:
    """Multi-source relaxation BFS. score[sid] = best over anchors of the product of
    (decay · relation_weight · confidence) along the shortest strong path. Anchors score 1.0."""
    best = {a: 1.0 for a in anchors}
    frontier = [(a, 0) for a in anchors]
    while frontier:
        sid, depth = frontier.pop(0)
        if depth >= MAX_DEPTH:
            continue
        base = best[sid]
        for nb, etype, conf in _edges_both(store, sid):
            cand = base * DISTANCE_DECAY * RELATION_WEIGHT.get(etype, 0.5) * (conf or 0.0)
            if cand > best.get(nb, 0.0):
                best[nb] = cand
                frontier.append((nb, depth + 1))
    return best


def path_scores(store: GraphStore, repo_id: str, matched_paths: Iterable[str],
                ws: WorkingSet) -> dict:
    """Relevance score per matched path (higher = keep first). Empty when there is no working
    set or nothing overlaps the graph — the reducer then falls back to plain file order."""
    matched = [m for m in matched_paths if m]
    if ws.empty or not matched:
        return {}
    anchors = _anchor_symbols(store, repo_id, ws)
    if not anchors:
        return {}
    prox = _proximity(store, anchors)
    # collapse symbol scores onto their file (a file's score = its most relevant symbol)
    by_file: dict = {}
    for row in store.symbols(repo_id):
        s = prox.get(row["symbol_id"])
        if s:
            p = _norm(row["path"])
            if s > by_file.get(p, 0.0):
                by_file[p] = s
    if not by_file:
        return {}
    out: dict = {}
    for mp in matched:
        nmp = _norm(mp)
        sc = by_file.get(nmp)
        if sc is None:                               # forgiving suffix alignment
            for p, s in by_file.items():
                if p.endswith(nmp) or nmp.endswith(p):
                    sc = max(sc or 0.0, s)
        if sc:
            out[mp] = sc
    return out


# --------------------------------------------------------------- simple-vs-graph comparison
def _kept_match_lines(raw: str, reduced) -> list:
    """The raw match lines that survived into a reduced summary (order as in raw). Used to
    diff simple vs graph retention without threading kept-sets through the reducer."""
    red_lines = set(reduced.reduced_text.splitlines())
    return [ln for ln in raw.splitlines() if ln.strip() and ln in red_lines]


def compare_search(raw: str, store: GraphStore, repo_id: str, ws: WorkingSet, *,
                   budget_tokens: int = 256, representation: str = "search") -> dict:
    """Deterministic simple-vs-graph A/B on one search output — the B1.2 deliverable, and the
    per-call primitive the offline 50-run replay sums over. Both arms use the SAME reducer on
    the SAME budget; the only difference is whether graph relevance orders retention. Reports
    which match lines graph ranking PROMOTED into the kept set that plain file order dropped."""
    from .library import reduce_search, search_matched_paths
    scores = path_scores(store, repo_id, search_matched_paths(raw), ws)
    simple = reduce_search(raw, {}, budget_tokens=budget_tokens, representation=representation)
    graph = reduce_search(raw, {}, budget_tokens=budget_tokens, representation=representation,
                          path_scores=scores or None)
    kept_simple = _kept_match_lines(raw, simple)
    kept_graph = _kept_match_lines(raw, graph)
    promoted = [ln for ln in kept_graph if ln not in set(kept_simple)]
    return {
        "graph_active": bool(scores),
        "scored_paths": len(scores),
        "simple": simple,
        "graph": graph,
        "kept_simple": kept_simple,
        "kept_graph": kept_graph,
        "promoted": promoted,           # relevant matches graph kept that simple dropped
    }


# --------------------------------------------------------------- live TOUCHED (best-effort)
def touched_from_journal(journal_db: Optional[str], session_id: Optional[str],
                         limit: int = 500) -> frozenset:
    """Read the session's already-read/edited paths from the HookJournal. Opens the sqlite
    directly (read-only, no schema guard) and swallows every error — this only informs
    ranking, never safety, so a missing/locked/older journal just yields no anchors."""
    if not journal_db or not session_id:
        return frozenset()
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT path_normalized FROM tool_events "
                "WHERE session_id=? AND kind IN ('read','edit') "
                "AND path_normalized IS NOT NULL LIMIT ?",
                (session_id, limit)).fetchall()
        finally:
            conn.close()
        return frozenset(_norm(r[0]) for r in rows if r[0])
    except Exception:                                # noqa: BLE001 — ranking is best-effort
        return frozenset()
