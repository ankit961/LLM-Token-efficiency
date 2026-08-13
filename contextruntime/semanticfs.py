"""SemanticFS — the model-facing read surface (design v1.2 §8). Phase 2.

FIRST primitive (implemented now): ``context_expand`` — resolve a ``result://<hash>``
or ``ctx://...`` handle back to its payload from the CAS. This must exist before any
enforced reduction: a reducer that removes information and hands the model a pointer it
cannot follow is unusable. Handles are the escape hatch that keeps reduction lossless.

Deferred (Phase 2 build-out): read_symbol / read_slice / find_callers over the
CodeSymbol graph with progressive resolution (L0 id … L5 file) and budgeted
DEPENDS_ON bundles. Those need the language adapters; this module holds the surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .store import GraphStore


@dataclass
class Expansion:
    handle: str
    found: bool
    kind: Optional[str] = None
    byte_size: int = 0
    text: str = ""                     # redacted at rest (stored redacted)
    note: str = ""


def _hash_of(handle: str) -> Optional[str]:
    for prefix in ("result://", "ctx://blob/"):
        if handle.startswith(prefix):
            return handle[len(prefix):]
    return None


def context_expand(store: GraphStore, handle: str) -> Expansion:
    """Resolve a handle to its payload. Never returns a silent empty: an expired or
    unknown handle is reported explicitly so the model can re-run rather than loop
    (design §3, MCP surface note)."""
    h = _hash_of(handle)
    if h is None:
        return Expansion(handle, False, note="unrecognized handle scheme")
    row = store.blob(h)
    if row is None:
        return Expansion(handle, False,
                         note="expired or evicted — re-run the source operation")
    kind = None
    o = store.conn.execute(
        "SELECT kind FROM objects WHERE content_hash=? LIMIT 1", (h,)).fetchone()
    if o:
        kind = o["kind"]
    return Expansion(handle, True, kind=kind, byte_size=row["byte_size"],
                     text=row["sample"] or "",
                     note="payload is bounded (CAS cap) and redacted at rest")
