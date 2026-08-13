"""Representation materializer (design v1.2 §8, Phase 2.3 / 2.3.1).

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

MATERIALIZATION HONESTY (2.3.1): the CAS bounds each stored segment, and the heuristic
adapter only captures declarations. So a rendered "implementation" is not always the
whole symbol. render_symbol therefore reports a `materialization_quality`
(complete_ast · complete_tree_sitter · declaration_only_heuristic · truncated) and, when
the body is not actually complete, appends an explicit marker rather than passing a
bounded prefix off as the full implementation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingest import est_tokens

LEVELS = ("identity", "signature", "skeleton", "slice", "implementation", "file")
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

# Must match the char cap the builder applies when storing a symbol's source (source[:8000]).
_CAS_CHAR_CAP = 8000

_STRUCT = re.compile(r"^\s*(def |class |if |elif |else|for |while |try|except|finally|"
                     r"with |return|raise|yield|await |function |=>|switch|case )")
_CALLISH = re.compile(r"[A-Za-z_$][\w$]*\s*\(")

_COMPLETE = {"python_ast": "complete_ast", "tree_sitter": "complete_tree_sitter",
             "regex_heuristic": "declaration_only_heuristic"}


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
    materialization_quality: str = "unknown"


def _quality(parser: str, truncated: bool, verified_complete: bool) -> str:
    # Completeness is ASSERTED only when the whole raw source is provably stored. Otherwise we
    # say "unverified" rather than falsely "complete".
    if truncated:
        return "truncated"
    if parser == "regex_heuristic":
        return "declaration_only_heuristic"
    if parser in ("python_ast", "tree_sitter"):
        return _COMPLETE[parser] if verified_complete else "unverified"
    return "unknown"


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
    parser = symbol_row["parser"]
    handle = f"ctx://symbol/{symbol_row['symbol_id']}"
    # Defense in depth: an unrecognized level must never silently mean "full implementation"
    # (its downstream default). Coerce to the bounded signature.
    if level not in LEVELS:
        level = "signature"
    prov = {"path": symbol_row["path"], "start_line": symbol_row["start_line"],
            "end_line": symbol_row["end_line"], "content_hash": symbol_row["content_hash"],
            "parser": parser, "resolution_quality": symbol_row["resolution_quality"],
            "language": symbol_row["language"]}

    # identity makes no source claim — just the name; no CAS read, no completeness flag.
    if level == "identity":
        prov["materialization_quality"] = _COMPLETE.get(parser, "unknown")
        return Rendered(symbol_row["symbol_id"], qn, level, qn, est_tokens(qn),
                        set(), prov, handle, prov["materialization_quality"])

    blob = store.blob(symbol_row["content_hash"]) if symbol_row["content_hash"] else None
    src = (blob["sample"] if blob else None) or (symbol_row["signature"] or qn)
    lines = src.splitlines() or [src]

    start = symbol_row["start_line"] or 1
    end = symbol_row["end_line"]
    span = (end - start + 1) if (end is not None and end >= start) else None
    original_bytes = blob["byte_size"] if blob else len(src.encode("utf-8", "replace"))
    stored_bytes = len((blob["sample"] if (blob and blob["sample"]) else src).encode("utf-8", "replace"))
    # Completeness from the TRUTHFUL full-size signal. The builder stores byte_size = full raw
    # source bytes and sample = redact(source[:CAP chars]); redaction can SHRINK the sample, so
    # neither len(sample) nor a line count is reliable on its own. byte_size is (bytes ≥ chars):
    #   byte_size ≤ CAP    → raw chars ≤ CAP → the WHOLE source was stored (complete)
    #   byte_size > CAP*4  → raw chars > CAP → definitely truncated (even a single huge line)
    #   in between         → ambiguous (long-line truncation vs multibyte-complete) → unverified
    # A stored line count below the declared span is an additional, independent truncation signal.
    full_stored = bool(blob) and original_bytes <= _CAS_CHAR_CAP
    # A line-count shortfall signals truncation ONLY when the source was not fully stored. If
    # full_stored, fewer stored lines just means redaction COLLAPSED a multi-line secret (e.g. a
    # PEM block → one [REDACTED:pem] token) — that is not truncation, so don't false-alarm on it.
    line_shortfall = bool(blob) and (not full_stored) and span is not None and len(lines) < span
    definitely_over = bool(blob) and original_bytes > _CAS_CHAR_CAP * 4
    truncated = line_shortfall or definitely_over
    verified_complete = (not truncated) and full_stored
    quality = _quality(parser, truncated, verified_complete)
    complete = quality in ("complete_ast", "complete_tree_sitter")
    prov["materialization_quality"] = quality
    prov["source"] = {"complete": complete, "original_bytes": original_bytes,
                      "stored_bytes": stored_bytes, "stored_lines": len(lines), "span_lines": span}

    keep = _line_set(lines, level)
    if level == "signature" and not blob:
        keep = {0}                       # only a signature is stored — render it as-is
    text = _render_text(lines, keep, start)

    # Never pass a bounded prefix, a heuristic declaration, or an unverifiable extent off as
    # the full body — say so in-band at the levels that claim completeness.
    if level in ("implementation", "file"):
        if truncated:
            span_txt = span if span is not None else "?"
            text += (f"\n# … source truncated: stored {len(lines)} of {span_txt} lines; "
                     f"read the file slice for the remainder …")
        elif quality == "declaration_only_heuristic":
            text += "\n# … heuristic parse: declaration only; function body not extracted …"
        elif quality == "unverified":
            text += "\n# … completeness unverified: symbol extent unknown; treat as possibly partial …"

    abs_lines = {start + i for i in keep}
    return Rendered(symbol_row["symbol_id"], qn, level, text, est_tokens(text),
                    abs_lines, prov, handle, quality)
