"""G1 — deterministic anchor resolution: turn a (path, line) / traceback / path+symbol / bare symbol
/ file / free-text into a graph ROOT symbol, so the compiler can project the useful context around it.

Local/lexical work here is an INDEXING primitive — it never reaches model context. This module does
NOT modify the frozen B1 search reducer or the B2 artifacts; it only reads the graph.
"""
from __future__ import annotations

import re
from typing import Optional


def _norm(p: str) -> str:
    p = (p or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _path_match(a: str, b: str) -> bool:
    """Component-aware path match: equal, or one is a '/'-delimited suffix of the other (so an
    absolute worktree path lines up with a repo-relative symbol path, but `notfoo.py` ≠ `foo.py`)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def symbol_at(store, path: str, line: int, repo_id: Optional[str] = None):
    """The NARROWEST symbol whose file matches `path` and whose span encloses `line`
    (`start_line <= line <= end_line`). Returns a sqlite3.Row or None.

    Deterministic selection when several symbols enclose the line (a method inside a class inside a
    module): smallest span wins (innermost); ties broken by the later start_line (more nested), then
    the longer qualified_name (more specific), then symbol_id. Path matching is component-aware so
    repo-relative vs absolute/normalized forms agree; a bare-suffix ambiguity across repos is resolved
    by `repo_id` when given."""
    try:
        line = int(line)
    except (TypeError, ValueError):
        return None
    if not path:
        return None
    q = ("SELECT * FROM symbols WHERE start_line IS NOT NULL AND end_line IS NOT NULL "
         "AND start_line <= ? AND end_line >= ?")
    args = [line, line]
    if repo_id:
        q += " AND repo_id=?"
        args.append(repo_id)
    cands = [r for r in store.conn.execute(q, args) if _path_match(path, r["path"])]
    if not cands:
        return None
    cands.sort(key=lambda r: (r["end_line"] - r["start_line"], -r["start_line"],
                              -len(r["qualified_name"] or ""), r["symbol_id"]))
    return cands[0]


# ---------------------------------------------------------------- traceback frames
_TB_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\S+))?')     # CPython traceback frame


def traceback_anchors(text: str):
    """(path, line, func) for every CPython traceback frame in `text`, in order. The LAST frame is
    usually the raising site; callers may prefer it. Repo-relative resolution is left to `symbol_at`
    (its path match is suffix-aware), so absolute paths in the traceback still line up."""
    return [(m.group(1), int(m.group(2)), m.group(3)) for m in _TB_FRAME.finditer(text or "")]


# ---------------------------------------------------------------- free-text lexical candidates
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# words that are identifiers lexically but carry no localizing signal
_STOP = frozenset("""the and for with this that from into your when will been have has are was were
    error errors bug issue fix test tests should would could return returns value values true false
    none null self cls def class async await import object list dict str int bool type kind name path
    line code file files data field fields model models django python method function call calls""".split())


def freetext_symbol_candidates(store, text: str, repo_id: Optional[str] = None, limit: int = 10) -> list:
    """LEXICAL (indexing) candidate roots from a free-text bug description — identifiers extracted
    from the text, matched against symbol qualified-name tails. Ranked by how specific the match is
    (exact tail > contains), then definitions over modules, then shorter qualified_name. This stays
    OUT of model context; it only proposes graph roots for the compiler. Deliberately simple — no
    embeddings — so it is deterministic and reviewable."""
    toks = [t for t in dict.fromkeys(_IDENT.findall(text or "")) if t.lower() not in _STOP and len(t) >= 4]
    if not toks:
        return []
    q = "SELECT symbol_id, qualified_name, path, kind FROM symbols"
    args: list = []
    if repo_id:
        q += " WHERE repo_id=?"
        args.append(repo_id)
    scored = []
    defkinds = ("class", "function", "method", "interface", "type")
    for r in store.conn.execute(q, args):
        qn = r["qualified_name"] or ""
        tail = qn.rsplit(".", 1)[-1]
        best = None
        for t in toks:
            if tail == t:
                best = min(best, 0) if best is not None else 0            # exact tail — strongest
            elif t in qn.split("."):
                best = min(best, 1) if best is not None else 1            # exact component
        if best is None:
            continue
        scored.append((best, 0 if r["kind"] in defkinds else 1, len(qn), r["symbol_id"],
                       {"symbol_id": r["symbol_id"], "qualified_name": qn, "path": r["path"], "kind": r["kind"]}))
    scored.sort(key=lambda x: x[:4])
    return [s[4] for s in scored[:limit]]
