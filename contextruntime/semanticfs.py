"""SemanticFS — the model-facing read surface (design v1.2 §8). Phase 2.3.

Turns the bundle PLANNER into a context COMPILER: read_symbol renders actual
source-derived text for a symbol and its budgeted dependency neighborhood, validates
the RENDERED token budget (not just the planner's estimate), and returns provenance +
expansion handles so the model can page progressively instead of reading whole files.

    read_symbol(store, symbol, budget, resolution="adaptive")   # primary path
    read_slice / find_callers / context_search / context_expand

No embeddings (design: keep Phase 2 clean). context_search is exact/short-name/path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .ingest import est_tokens
from .store import GraphStore

ESTIMATOR = "chars4-v1"
# render levels, low -> high (identity is the cheapest downgrade target)
DOWNGRADE = ("identity", "signature", "skeleton", "slice", "implementation")


# ---------------------------------------------------------------- expand

@dataclass
class Expansion:
    handle: str
    found: bool
    kind: Optional[str] = None
    byte_size: int = 0
    text: str = ""
    note: str = ""


def _blob_hash(handle: str) -> Optional[str]:
    for prefix in ("result://", "ctx://blob/"):
        if handle.startswith(prefix):
            return handle[len(prefix):]
    return None


def context_expand(store: GraphStore, handle: str) -> Expansion:
    """Resolve a handle to its payload. Never a silent empty — expired/unknown is
    reported so the model re-runs rather than loops. Handles:
      result://<hash> / ctx://blob/<hash>   -> CAS payload (tool result / source)
      ctx://symbol/<symbol_id>[@level]       -> rendered symbol
    """
    if handle.startswith("ctx://symbol/"):
        from .codegraph.render import render_symbol
        rest = handle[len("ctx://symbol/"):]
        sid, _, level = rest.partition("@")
        row = store.symbol_row(sid)
        if row is None:
            return Expansion(handle, False, note="unknown symbol handle")
        r = render_symbol(store, row, level or "implementation")
        return Expansion(handle, True, kind=row["kind"], byte_size=len(r.text),
                         text=r.text, note=f"rendered @{r.level}")
    h = _blob_hash(handle)
    if h is None:
        return Expansion(handle, False, note="unrecognized handle scheme")
    row = store.blob(h)
    if row is None:
        return Expansion(handle, False, note="expired or evicted — re-run the source operation")
    o = store.conn.execute(
        "SELECT kind FROM objects WHERE content_hash=? LIMIT 1", (h,)).fetchone()
    return Expansion(handle, True, kind=(o["kind"] if o else None),
                     byte_size=row["byte_size"], text=row["sample"] or "",
                     note="payload is bounded (CAS cap) and redacted at rest")


# ---------------------------------------------------------------- read surface

@dataclass
class ReadResult:
    root: str
    resolution: str
    ok: bool = True
    note: str = ""
    budget: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)      # rendered symbols
    ambiguity_hints: list = field(default_factory=list)
    expansion: dict = field(default_factory=dict)

    def to_text(self) -> str:
        parts = []
        for s in self.sections:
            head = f"# {s['qualified_name']}  @{s['level']}"
            if s.get("match"):
                head += f"  ({s['match']}{'/soft' if s.get('soft') else ''})"
            head += f"  [{s['handle']}]"
            parts.append(head + "\n" + s["text"])
        if self.ambiguity_hints:
            parts.append("# ambiguous (not resolved): " +
                         "; ".join(f"{h['name']}→{h['candidates']}" for h in self.ambiguity_hints))
        return "\n\n".join(parts)


def _resolve(store, symbol, repo_id=None):
    return store.symbol_row(symbol) or store.find_symbol(symbol, repo_id)


def read_symbol(store: GraphStore, symbol: str, budget: int = 2048,
                resolution: str = "adaptive", include_dependencies: bool = True,
                safety_margin: float = 0.10, repo_id: Optional[str] = None) -> ReadResult:
    from .codegraph.bundle import build_bundle
    from .codegraph.render import render_symbol

    row = _resolve(store, symbol, repo_id)
    if row is None:
        return ReadResult(root=symbol, resolution=resolution, ok=False,
                          note="symbol not found")

    # Fixed-resolution: render just this symbol at the requested level.
    if resolution in ("identity", "signature", "skeleton", "slice", "implementation"):
        r = render_symbol(store, row, resolution)
        rr = ReadResult(root=row["symbol_id"], resolution=resolution,
                        sections=[_section(r)], expansion={"available": True, "handles": [r.handle]})
        rr.budget = {"requested": budget, "planned_estimate": r.tokens,
                     "rendered_estimate": r.tokens, "estimator": ESTIMATOR,
                     "safety_margin": safety_margin, "planned_vs_rendered_error": 0.0}
        return rr

    # Adaptive: plan a budgeted neighborhood, then MATERIALIZE and validate the
    # rendered budget (shrink the least-important representation if the estimate ran over).
    if include_dependencies:
        b = build_bundle(store, row["symbol_id"], budget=budget)
        plan = [(s, s.level) for s in b.selected]
        planned = b.used
        graph = {"hard": b.metrics.get("hard_selected", 0),
                 "soft": b.metrics.get("soft_selected", 0),
                 "ambiguous": len(b.ambiguity_hints)}
        hints = b.ambiguity_hints
    else:
        plan = [(_FakeSel(row["symbol_id"], "implementation", 0, None, None, False), "implementation")]
        planned = 0
        graph = {"hard": 0, "soft": 0, "ambiguous": 0}
        hints = []

    # materialize
    levels = {sel.symbol_id: lvl for sel, lvl in plan}
    order = [sel for sel, _ in plan]
    rendered = {sel.symbol_id: render_symbol(store, store.symbol_row(sel.symbol_id), levels[sel.symbol_id])
                for sel in order if store.symbol_row(sel.symbol_id)}

    def total():
        return sum(rendered[s.symbol_id].tokens for s in order if s.symbol_id in rendered)

    # RENDERED BUDGET VALIDATOR: downgrade the least-important representation until the
    # RENDERED total fits. Deps first (soft, then ascending utility); the ROOT is
    # downgraded LAST — but it IS downgradable, so a tiny budget still yields a valid
    # (identity-level) result rather than overflowing.
    hard_budget = budget
    non_root = sorted([s for s in order if s.utility != float("inf")],
                      key=lambda s: (0 if s.soft else 1, s.utility))
    root_sels = [s for s in order if s.utility == float("inf")]
    shrink_order = non_root + root_sels
    guard = 0
    while total() > hard_budget and guard < 10000:
        guard += 1
        moved = False
        for s in shrink_order:
            if s.symbol_id not in rendered:
                continue
            cur = rendered[s.symbol_id].level
            idx = DOWNGRADE.index(cur) if cur in DOWNGRADE else len(DOWNGRADE) - 1
            if idx > 0:
                rendered[s.symbol_id] = render_symbol(
                    store, store.symbol_row(s.symbol_id), DOWNGRADE[idx - 1])
                moved = True
                if total() <= hard_budget:
                    break
        if not moved:
            # every section at identity and still over -> drop least-utility NON-root
            drop = min((s for s in non_root if s.symbol_id in rendered),
                       key=lambda s: s.utility, default=None)
            if drop is None:
                break                        # only root@identity left — nothing more to shed
            rendered.pop(drop.symbol_id, None)

    rendered_total = total()
    pre = abs(rendered_total - planned) / rendered_total if rendered_total else 0.0
    sections = [_section(rendered[s.symbol_id], s) for s in order if s.symbol_id in rendered]
    rr = ReadResult(root=row["symbol_id"], resolution="adaptive", sections=sections,
                    graph=graph, ambiguity_hints=hints,
                    expansion={"available": True,
                               "handles": [s["handle"] + "@implementation" for s in sections]})
    rr.budget = {"requested": budget, "planned_estimate": planned,
                 "rendered_estimate": rendered_total, "estimator": ESTIMATOR,
                 "safety_margin": safety_margin, "planned_vs_rendered_error": round(pre, 4)}
    return rr


@dataclass
class _FakeSel:
    symbol_id: str
    level: str
    utility: float
    edge: object
    match: object
    soft: bool


def _section(r, sel=None) -> dict:
    d = {"symbol_id": r.symbol_id, "qualified_name": r.qualified_name, "level": r.level,
         "text": r.text, "tokens": r.tokens, "provenance": r.provenance, "handle": r.handle}
    if sel is not None:
        d.update({"edge": getattr(sel, "edge", None), "match": getattr(sel, "match", None),
                  "soft": getattr(sel, "soft", False)})
    return d


def read_slice(store: GraphStore, symbol: str, budget: int = 512,
               repo_id: Optional[str] = None) -> ReadResult:
    return read_symbol(store, symbol, budget=budget, resolution="slice",
                       include_dependencies=False, repo_id=repo_id)


def find_callers(store: GraphStore, symbol: str, limit: int = 20,
                 repo_id: Optional[str] = None) -> list:
    row = _resolve(store, symbol, repo_id)
    if row is None:
        return []
    out = []
    for e in store.code_edges_to(row["symbol_id"], ("CALLS",))[:limit]:
        src = store.symbol_row(e["src_id"])
        if src is None:
            continue
        out.append({"qualified_name": src["qualified_name"], "path": src["path"],
                    "handle": f"ctx://symbol/{src['symbol_id']}",
                    "confidence": e["confidence"], "match": e["match_kind"]})
    return out


def context_search(store: GraphStore, query: str, repo_id: Optional[str] = None,
                   limit: int = 10) -> list:
    """Ranked symbol references — HANDLES, never code dumps (the model pages via
    read_symbol / context_expand)."""
    return [{"qualified_name": r["qualified_name"], "path": r["path"], "kind": r["kind"],
             "language": r["language"], "handle": f"ctx://symbol/{r['symbol_id']}"}
            for r in store.search_symbols(query, repo_id, limit)]
