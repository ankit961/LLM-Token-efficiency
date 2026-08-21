"""G1 — one-call graph-first context compilation.

`context_compile` resolves an ANCHOR (path:line / traceback / path+symbol / bare symbol / file /
free-text) to a graph ROOT, then REUSES the existing budgeted compiler (`semanticfs.read_symbol` →
`build_bundle` → `render_symbol`). It adds no new bundling machinery. The critical invariant is
inherited from `read_symbol`: the ROOT (target) is downgraded LAST, so the exact target
implementation is preserved whenever the budget permits — the opposite of B2's skeletonization.

Preferred bundle order (from the planner): target implementation → local/class context →
signatures/slices of high-confidence callees → callers/tests when the graph says they matter →
lower-value neighbors only if budget remains. `read_symbol.budget.budget_insufficient` reports when
the budget genuinely cannot hold the target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .anchors import _path_match, freetext_symbol_candidates, symbol_at, traceback_anchors


@dataclass
class CompileResult:
    ok: bool
    anchor_kind: str                       # how the root was resolved
    resolved_root: Optional[str] = None    # symbol_id
    root_qualified_name: Optional[str] = None
    read: object = None                    # the ReadResult (sections, budget, graph, expansion)
    note: str = ""
    provenance: dict = field(default_factory=dict)

    def bundle_text(self) -> str:
        return self.read.to_text() if self.read is not None else ""


def _file_root(store, path, repo_id):
    """A file-only anchor → the file's module symbol, else its largest-span symbol (the most likely
    place to start reading)."""
    q = "SELECT * FROM symbols WHERE start_line IS NOT NULL AND end_line IS NOT NULL"
    args = []
    if repo_id:
        q += " AND repo_id=?"
        args.append(repo_id)
    rows = [r for r in store.conn.execute(q, args) if _path_match(path, r["path"])]
    if not rows:
        return None
    mods = [r for r in rows if r["kind"] == "module"]
    if mods:
        return max(mods, key=lambda r: r["end_line"] - r["start_line"])
    return max(rows, key=lambda r: r["end_line"] - r["start_line"])


def resolve_anchor(store, *, path=None, line=None, symbol=None, query=None, traceback=None,
                   repo_id=None):
    """Deterministic anchor → (root_row, anchor_kind). Tried in strongest-signal order; the first
    that resolves wins. Lexical/free-text work stays out of model context."""
    if traceback:
        frames = traceback_anchors(traceback)
        if frames:
            p, ln, _fn = frames[-1]                    # raising site (last frame)
            r = symbol_at(store, p, ln, repo_id)
            if r is not None:
                return r, "traceback"
    if path and line is not None:
        r = symbol_at(store, path, line, repo_id)
        if r is not None:
            return r, "path_line"
    if symbol:
        from ..semanticfs import _resolve_candidates
        cands = _resolve_candidates(store, symbol, repo_id)
        if path:
            scoped = [c for c in cands if _path_match(path, c["path"])]
            cands = scoped or cands
        if cands:
            return cands[0], ("path_symbol" if path else "symbol")
    if path and line is None:
        r = _file_root(store, path, repo_id)
        if r is not None:
            return r, "file"
    if query:
        cands = freetext_symbol_candidates(store, query, repo_id, limit=1)
        if cands:
            r = store.symbol_row(cands[0]["symbol_id"])
            if r is not None:
                return r, "freetext"
    return None, "unresolved"


def context_compile(store, *, path=None, line=None, symbol=None, query=None, traceback=None,
                    budget: int = 2048, repo_id=None) -> CompileResult:
    """Resolve an anchor and compile a budgeted, graph-projected context bundle around it."""
    from ..semanticfs import read_symbol
    root, kind = resolve_anchor(store, path=path, line=line, symbol=symbol, query=query,
                                traceback=traceback, repo_id=repo_id)
    if root is None:
        return CompileResult(ok=False, anchor_kind=kind, note="anchor unresolved",
                             provenance={"budget": budget, "repo_id": repo_id})
    rr = read_symbol(store, root["symbol_id"], budget=budget, repo_id=repo_id)
    return CompileResult(
        ok=rr.ok, anchor_kind=kind, resolved_root=root["symbol_id"],
        root_qualified_name=root["qualified_name"], read=rr, note=rr.note,
        provenance={"budget": budget, "repo_id": repo_id,
                    "budget_insufficient": rr.budget.get("budget_insufficient"),
                    "serialized_tokens": rr.budget.get("serialized_tokens"),
                    "graph": rr.graph, "sections": len(rr.sections)})
