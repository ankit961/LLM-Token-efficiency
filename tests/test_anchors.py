"""G1 Step-2 — deterministic anchor resolution tests (synthetic store, no worktree)."""
from contextruntime.codegraph.anchors import (freetext_symbol_candidates, symbol_at,
                                              traceback_anchors)
from contextruntime.store import GraphStore

_COLS = ("symbol_id", "repo_id", "language", "kind", "qualified_name", "path",
         "start_line", "end_line", "signature", "content_hash", "parser", "resolution_quality",
         "schema_version")


def _store(rows):
    s = GraphStore(":memory:")
    for r in rows:
        vals = {**{c: None for c in _COLS}, "language": "python", "parser": "test",
                "resolution_quality": "exact", "schema_version": 1, **r}
        s.conn.execute(f"INSERT INTO symbols ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                       [vals[c] for c in _COLS])
    s.conn.commit()
    return s


# a module with a class containing a method, plus a nested function inside the method
_NEST = [
    dict(symbol_id="m", repo_id="r", kind="module", qualified_name="pkg.mod", path="pkg/mod.py", start_line=1, end_line=100),
    dict(symbol_id="c", repo_id="r", kind="class", qualified_name="pkg.mod.C", path="pkg/mod.py", start_line=10, end_line=50),
    dict(symbol_id="meth", repo_id="r", kind="method", qualified_name="pkg.mod.C.method", path="pkg/mod.py", start_line=20, end_line=40),
    dict(symbol_id="inner", repo_id="r", kind="function", qualified_name="pkg.mod.C.method.inner", path="pkg/mod.py", start_line=25, end_line=30),
]


def test_symbol_at_narrowest_enclosing():
    s = _store(_NEST)
    assert symbol_at(s, "pkg/mod.py", 27)["symbol_id"] == "inner"    # innermost (nested fn)
    assert symbol_at(s, "pkg/mod.py", 22)["symbol_id"] == "meth"     # method body, outside inner
    assert symbol_at(s, "pkg/mod.py", 15)["symbol_id"] == "c"        # class body, outside method
    assert symbol_at(s, "pkg/mod.py", 5)["symbol_id"] == "m"         # module level
    assert symbol_at(s, "pkg/mod.py", 200) is None                   # past every span
    assert symbol_at(s, "pkg/mod.py", 0) is None


def test_symbol_at_path_forms_and_boundaries():
    s = _store(_NEST)
    assert symbol_at(s, "/abs/wt/pkg/mod.py", 27)["symbol_id"] == "inner"   # absolute suffix match
    assert symbol_at(s, "./pkg/mod.py", 27)["symbol_id"] == "inner"         # ./ normalized
    assert symbol_at(s, "other/mod.py", 27) is None                        # basename-only ≠ different dir
    assert symbol_at(s, "pkg/mod.py", 20)["symbol_id"] == "meth"           # inclusive lower boundary
    assert symbol_at(s, "pkg/mod.py", 40)["symbol_id"] == "meth"           # inclusive upper boundary


def test_symbol_at_multi_repo_disambiguation():
    s = _store(_NEST + [
        dict(symbol_id="m2", repo_id="r2", kind="module", qualified_name="pkg.mod", path="pkg/mod.py", start_line=1, end_line=100),
        dict(symbol_id="f2", repo_id="r2", kind="function", qualified_name="pkg.mod.f", path="pkg/mod.py", start_line=27, end_line=29),
    ])
    assert symbol_at(s, "pkg/mod.py", 27, repo_id="r")["symbol_id"] == "inner"   # scoped to repo r
    assert symbol_at(s, "pkg/mod.py", 27, repo_id="r2")["symbol_id"] == "f2"     # scoped to repo r2
    # without repo_id both repos' enclosing symbols compete; narrowest wins deterministically
    assert symbol_at(s, "pkg/mod.py", 27)["symbol_id"] in ("inner", "f2")


def test_traceback_anchors():
    tb = ('Traceback (most recent call last):\n'
          '  File "django/db/models/query.py", line 400, in filter\n'
          '    return self._filter_or_exclude(False, args, kwargs)\n'
          '  File "/abs/django/db/models/sql/query.py", line 12, in add_q\n')
    assert traceback_anchors(tb) == [("django/db/models/query.py", 400, "filter"),
                                     ("/abs/django/db/models/sql/query.py", 12, "add_q")]
    assert traceback_anchors("no frames here") == []


def test_freetext_candidates_rank_definitions():
    s = _store([
        dict(symbol_id="qs", repo_id="r", kind="class", qualified_name="pkg.query.QuerySet", path="pkg/query.py", start_line=1, end_line=9),
        dict(symbol_id="qsu", repo_id="r", kind="method", qualified_name="pkg.query.QuerySet.union", path="pkg/query.py", start_line=3, end_line=5),
        dict(symbol_id="mod", repo_id="r", kind="module", qualified_name="pkg.union_helpers", path="pkg/union_helpers.py", start_line=1, end_line=9),
    ])
    got = [c["qualified_name"] for c in freetext_symbol_candidates(s, "QuerySet union is broken", repo_id="r")]
    assert "pkg.query.QuerySet" in got and "pkg.query.QuerySet.union" in got
    assert got.index("pkg.query.QuerySet") < got.index("pkg.query.QuerySet.union")   # exact-tail class first
    assert freetext_symbol_candidates(s, "the and for this", repo_id="r") == []        # only stopwords ⇒ none
