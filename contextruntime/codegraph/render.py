"""Representation materializer (design v1.2 §8, Phase 2.3).

Turns a planned (symbol, level) selection into ACTUAL source-derived text. This is
where the planner becomes a context compiler: real code, not metadata.

Levels are defined over the symbol's SOURCE LINES as strictly nested sets, so the
rendered representations satisfy CONTENT MONOTONICITY:

    lines(L0) ⊆ lines(L1) ⊆ lines(L2) ⊆ lines(L3) ⊆ lines(L4)   (⊆ L5 = whole file)

    L0 identity      just the qualified name (no source)
    L1 signature     the declaration/header line(s)
    L2 skeleton      header + structural lines (control flow / calls), bodies elided
    L3 slice         skeleton + a contiguous relevant region
    L4 implementation the full symbol source
    L5 file          the whole file (escalation only; not materialized here)

Source comes from the CAS (stored redacted at index time). Omitted lines are shown as
an elision marker so the text stays readable without inventing content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingest import est_tokens

LEVELS = ("identity", "signature", "skeleton", "slice", "implementation", "file")
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

_STRUCT = re.compile(r"^\s*(def |class |if |elif |else|for |while |try|except|finally|"
                     r"with |return|raise|yield|await |function |=>|switch|case )")
_CALLISH = re.compile(r"[A-Za-z_$][\w$]*\s*\(")


@dataclass
class Rendered:
    symbol_id: str
    qualified_name: str
    level: str
    text: str
    tokens: int
    included_lines: set = field(default_factory=set)   # absolute source line numbers
    provenance: dict = field(default_factory=dict)
    handle: str = ""


def _line_set(lines: list[str], level: str) -> set[int]:
    """Return the set of line indices to include at `level` (nested by construction)."""
    n = len(lines)
    if n == 0:
        return set()
    header = {0}
    i = 0
    # extend header across a multi-line signature (until the first ':' or '{')
    while i < n and not re.search(r"[:{]\s*$", lines[i]) and i < 3:
        header.add(i); i += 1
    header.add(min(i, n - 1))
    if level in ("identity",):
        return set()
    if level == "signature":
        return set(header)
    structural = {j for j, ln in enumerate(lines)
                  if _STRUCT.match(ln) or _CALLISH.search(ln)}
    if level == "skeleton":
        return header | structural
    if level == "slice":
        region = set(range(0, min(n, max(len(header) + 1, int(n * 0.6)))))
        return header | structural | region
    return set(range(n))                # implementation / file -> all


def _render_text(lines: list[str], keep: set[int], start_line: int) -> str:
    if not keep:
        return ""
    out, i, n = [], 0, len(lines)
    while i < n:
        if i in keep:
            out.append(lines[i]); i += 1
        else:
            j = i
            while j < n and j not in keep:
                j += 1
            out.append(f"    # … {j - i} line{'s' if j - i > 1 else ''} …")
            i = j
    return "\n".join(out)


def render_symbol(store, symbol_row, level: str, *, task_terms=None) -> Rendered:
    qn = symbol_row["qualified_name"]
    prov = {"path": symbol_row["path"], "start_line": symbol_row["start_line"],
            "end_line": symbol_row["end_line"], "content_hash": symbol_row["content_hash"],
            "parser": symbol_row["parser"], "resolution_quality": symbol_row["resolution_quality"],
            "language": symbol_row["language"]}
    handle = f"ctx://symbol/{symbol_row['symbol_id']}"

    if level == "identity":
        text = qn
        return Rendered(symbol_row["symbol_id"], qn, level, text, est_tokens(text),
                        set(), prov, handle)

    blob = store.blob(symbol_row["content_hash"]) if symbol_row["content_hash"] else None
    src = (blob["sample"] if blob else None) or (symbol_row["signature"] or qn)
    lines = src.splitlines() or [src]
    keep = _line_set(lines, level)
    if level == "signature" and not blob:
        keep = {0}                       # only a signature is stored — render it as-is
    text = _render_text(lines, keep, symbol_row["start_line"] or 1)
    abs_lines = {(symbol_row["start_line"] or 1) + i for i in keep}
    return Rendered(symbol_row["symbol_id"], qn, level, text, est_tokens(text),
                    abs_lines, prov, handle)
