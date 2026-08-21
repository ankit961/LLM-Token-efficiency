"""G1 offline harness — metric primitives (deterministic, no graph/transcript needed)."""
from corpus.g1_replay import (_lineno_of, _read_line_span, available_anchors, coedit_relation,
                              edit_line_coverage)
from contextruntime.store import GraphStore

_COLS = ("symbol_id", "repo_id", "language", "kind", "qualified_name", "path", "start_line",
         "end_line", "signature", "content_hash", "parser", "resolution_quality", "schema_version")


def _sym(s, sid, path, qn):
    vals = {**{c: None for c in _COLS}, "symbol_id": sid, "repo_id": "r", "language": "python",
            "kind": "function", "qualified_name": qn, "path": path, "start_line": 1, "end_line": 5,
            "parser": "test", "resolution_quality": "exact", "schema_version": 1}
    s.conn.execute(f"INSERT INTO symbols ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                   [vals[c] for c in _COLS])


def test_coedit_relation_classifies_strongest_edge():
    s = GraphStore(":memory:")
    _sym(s, "a", "pkg/x.py", "pkg.x.a"); _sym(s, "b", "pkg/x.py", "pkg.x.b")
    _sym(s, "c", "pkg/y.py", "pkg.y.c"); _sym(s, "d", "pkg/z.py", "pkg.z.d")
    s.add_code_edge("r", "a", "c", "CALLS", 1.0, "exact", match_kind="exact")     # a → c
    s.conn.commit()
    assert coedit_relation(s, "a", "c") == "callee"      # a depends on c
    assert coedit_relation(s, "c", "a") == "caller"      # c is reached FROM a
    assert coedit_relation(s, "a", "b") == "same_file"   # same path, no edge
    assert coedit_relation(s, "a", "d") == "none"        # different file, no edge


def test_lineno_of_reads_line_number_prefix():
    content = "   10→def foo():\n   11→    return bar()\n   12→x = 1\n"
    assert _lineno_of(content, "    return bar()") == 11
    assert _lineno_of(content, "def foo():") == 10
    assert _lineno_of(content, "not present") is None
    assert _lineno_of("no line numbers here", "x") is None


def test_read_line_span():
    assert _read_line_span("   10→a\n   25→b\n   17→c\n") == (10, 25)
    assert _read_line_span("no numbers") is None


def test_edit_line_coverage_lstrip_and_fraction():
    bundle = "def foo(self):\n    return compute() + helper()\n"
    # exact block (indentation differs) ⇒ full coverage via lstrip match
    assert edit_line_coverage(bundle, "    def foo(self):\n        return compute() + helper()") == 1.0
    # one of two lines present ⇒ 0.5
    assert edit_line_coverage(bundle, "return compute() + helper()\nx = missing_line()") == 0.5
    assert edit_line_coverage(bundle, "\n   \n") == 0.0            # only blanks
    assert edit_line_coverage("", "return x") == 0.0


def test_available_anchors_detects_traceback_and_fileline():
    ps = ('Crash:\n  File "django/db/models/query.py", line 400, in filter\n'
          'see also foo/bar.py:12 for context')
    a = available_anchors(ps)
    assert a["has_traceback"] and a["traceback_frames"][0] == ("django/db/models/query.py", 400, "filter")
    assert a["has_file_line"] and ("foo/bar.py", 12) in a["file_line_refs"]
    assert a["problem_tokens"] > 0

    plain = available_anchors("Just a prose bug description with no code locations.")
    assert not plain["has_traceback"] and not plain["has_file_line"]
