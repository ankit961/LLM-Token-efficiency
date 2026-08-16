"""ContextPolicy semantic probe — turn a native source read into a policy ToolCall.

Given a file the agent read (or is about to read) raw, ask the code-graph: is this file indexed,
and would a compact SEMANTIC equivalent be smaller than the raw dump? The cheapest honest semantic
equivalent to "read the whole file" is its SIGNATURE INDEX — one signature (or qualified name) line
per top-level definition — which orients the agent for a fraction of the tokens; from there it can
``read_symbol`` the one thing it actually needs.

Read-only: queries the frozen code-graph via ``store.conn``; writes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ingest import est_tokens

# symbol kinds that represent a navigable definition (a module row is the file itself, excluded)
DEF_KINDS = ("function", "method", "class", "interface", "type", "constant", "test")


@dataclass
class SourceReadProbe:
    path: str
    indexed: bool
    n_defs: int
    raw_tokens: int
    skeleton_tokens: int
    defs: list = field(default_factory=list)   # (qualified_name, kind, start_line, end_line)

    @property
    def is_opportunity(self) -> bool:
        # a real steer only when the file is indexed with navigable defs AND the index is smaller
        return self.indexed and self.n_defs > 0 and 0 < self.skeleton_tokens < self.raw_tokens

    @property
    def avoidable_tokens(self) -> int:
        return max(0, self.raw_tokens - self.skeleton_tokens) if self.is_opportunity else 0


def symbols_for_path(store, repo_id: str, path: str) -> list:
    return store.conn.execute(
        "SELECT kind, qualified_name, signature, start_line, end_line "
        "FROM symbols WHERE repo_id=? AND path=?", (repo_id, path)).fetchall()


def probe_source_read(store, repo_id: str, path: str, *, raw_tokens: int) -> SourceReadProbe:
    """raw_tokens is the model-visible token count of the native read (from the HookJournal)."""
    rows = symbols_for_path(store, repo_id, path)
    if not rows:
        return SourceReadProbe(path, indexed=False, n_defs=0, raw_tokens=raw_tokens,
                               skeleton_tokens=0, defs=[])
    defs = [r for r in rows if r[0] in DEF_KINDS]
    # skeleton cost: one signature line per navigable def (fall back to the qualified name)
    skel = sum(est_tokens((r[2] or r[1] or "") + "\n") for r in defs)
    return SourceReadProbe(
        path, indexed=True, n_defs=len(defs), raw_tokens=raw_tokens,
        skeleton_tokens=skel,
        defs=[(r[1], r[0], r[3], r[4]) for r in defs])


def probe_to_toolcall(probe: SourceReadProbe, *, is_edit_target: bool = False):
    """Adapt a probe into a policy.ToolCall so policy.decide can rule on it uniformly."""
    from .policy import ToolCall
    return ToolCall(
        kind="read", target=probe.path, token_est=probe.raw_tokens,
        is_edit_target=is_edit_target, is_source=probe.indexed,
        semantic_available=probe.is_opportunity,
        semantic_bundle_tokens=probe.skeleton_tokens if probe.indexed else None)
