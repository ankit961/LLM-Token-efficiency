"""G2 task-relevance-graph harness — layer/ceiling/budget primitives (deterministic, synthetic)."""
from corpus.g2_replay import (budget_recall, candidate_pool, ceiling_recall, cochange_files)
from contextruntime.store import GraphStore

_COLS = ("symbol_id", "repo_id", "language", "kind", "qualified_name", "path", "start_line",
         "end_line", "signature", "content_hash", "parser", "resolution_quality", "schema_version")


def _sym(s, sid, path, qn, kind="function"):
    vals = {**{c: None for c in _COLS}, "symbol_id": sid, "repo_id": "r", "language": "python",
            "kind": kind, "qualified_name": qn, "path": path, "start_line": 1, "end_line": 5,
            "parser": "test", "resolution_quality": "exact", "schema_version": 1}
    s.conn.execute(f"INSERT INTO symbols ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                   [vals[c] for c in _COLS])


def _store():
    s = GraphStore(":memory:")
    _sym(s, "a", "pkg/x.py", "pkg.x.a")          # anchor (edited)
    _sym(s, "b", "pkg/x.py", "pkg.x.b")          # same-file support (locality)
    _sym(s, "c", "pkg/y.py", "pkg.y.c")          # callee support (structural)
    _sym(s, "d", "pkg/z.py", "pkg.z.d")          # only reachable via co-change
    s.add_code_edge("r", "a", "c", "CALLS", 1.0, "exact", match_kind="exact")
    s.conn.commit()
    return s


def test_layers_are_nested_and_reach_expected_support():
    s = _store()
    anchors, needed = {"a"}, {"b", "c", "d"}
    # Gstruct: only the callee c (1/3)
    assert ceiling_recall(s, anchors, needed, "Gstruct", "r") == round(1 / 3, 4)
    # Glocal: callee c + same-file b (2/3) — d still unreachable
    assert ceiling_recall(s, anchors, needed, "Glocal", "r") == round(2 / 3, 4)
    # Gtask WITHOUT co-change == Glocal here (no tests/idents link d)
    assert ceiling_recall(s, anchors, needed, "Gtask", "r") == round(2 / 3, 4)
    # Gtask WITH co-change of pkg/z.py reaches d too (3/3)
    assert ceiling_recall(s, anchors, needed, "Gtask", "r", ["pkg/z.py"]) == 1.0
    # lexical (target only) reaches no support
    assert ceiling_recall(s, anchors, needed, "lexical", "r") == 0.0


def test_cochange_adds_candidate_at_rank_4():
    s = _store()
    pool_local = candidate_pool(s, {"a"}, "Glocal", "r")
    pool_task = candidate_pool(s, {"a"}, "Gtask", "r", ["pkg/z.py"])
    assert "d" not in pool_local                          # locality never reaches pkg/z.py
    assert pool_task.get("d") == 4                         # co-change surfaces it at rank 4
    assert "a" not in pool_task                            # anchors are excluded from their own pool


def test_budget_recall_never_exceeds_ceiling():
    s = _store()
    anchors, needed = {"a"}, {"b", "c", "d"}
    for layer in ("Gstruct", "Glocal", "Gtask"):
        ceil = ceiling_recall(s, anchors, needed, layer, "r", ["pkg/z.py"])
        R, toks, _inc = budget_recall(s, anchors, needed, layer, 4096, "r", ["pkg/z.py"])
        assert R <= ceil + 1e-9                            # a budget can only realize part of the ceiling
        assert toks > 0


def test_cochange_files_failopen_on_missing_mirror():
    assert cochange_files(None, "deadbeef", ["pkg/x.py"]) == []
    assert cochange_files("/no/such/mirror", "deadbeef", ["pkg/x.py"]) == []
    assert cochange_files("/tmp", None, ["pkg/x.py"]) == []
