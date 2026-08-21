"""G1 Step-3 — anchor resolution order + one-call compilation (synthetic store with a real blob)."""
from contextruntime.codegraph.compile import context_compile, resolve_anchor
from contextruntime.store import GraphStore

_COLS = ("symbol_id", "repo_id", "language", "kind", "qualified_name", "path", "start_line",
         "end_line", "signature", "content_hash", "parser", "resolution_quality", "schema_version")


def _store():
    s = GraphStore(":memory:")
    body = "def target():\n    return compute() + 1\n"
    s.put_blob("h1", len(body), body)                          # the target's source blob
    rows = [
        dict(symbol_id="mod", repo_id="r", kind="module", qualified_name="pkg.mod", path="pkg/mod.py", start_line=1, end_line=50),
        dict(symbol_id="tgt", repo_id="r", kind="function", qualified_name="pkg.mod.target", path="pkg/mod.py", start_line=10, end_line=20, content_hash="h1", signature="def target()"),
    ]
    for r in rows:
        vals = {**{c: None for c in _COLS}, "language": "python", "parser": "test",
                "resolution_quality": "exact", "schema_version": 1, **r}
        s.conn.execute(f"INSERT INTO symbols ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                       [vals[c] for c in _COLS])
    s.conn.commit()
    return s


def test_resolve_anchor_order_and_precision():
    s = _store()
    assert resolve_anchor(s, path="pkg/mod.py", line=15)[0]["symbol_id"] == "tgt"      # path:line → narrowest
    assert resolve_anchor(s, path="pkg/mod.py", line=15)[1] == "path_line"
    assert resolve_anchor(s, symbol="target")[0]["symbol_id"] == "tgt"                 # bare symbol
    assert resolve_anchor(s, path="pkg/mod.py", symbol="target")[1] == "path_symbol"
    assert resolve_anchor(s, query="target is broken")[1] == "freetext"
    tb = '  File "pkg/mod.py", line 15, in target'
    assert resolve_anchor(s, traceback=tb)[0]["symbol_id"] == "tgt" and resolve_anchor(s, traceback=tb)[1] == "traceback"
    assert resolve_anchor(s, path="pkg/mod.py")[1] == "file"                           # file-only → root
    assert resolve_anchor(s, symbol="nope")[0] is None                                 # unresolved


def test_context_compile_preserves_target_body():
    s = _store()
    r = context_compile(s, path="pkg/mod.py", line=15, budget=2048, repo_id="r")
    assert r.ok and r.resolved_root == "tgt" and r.anchor_kind == "path_line"
    assert "return compute() + 1" in r.bundle_text()             # exact target body preserved
    assert r.provenance["serialized_tokens"] <= 2048


def test_context_compile_unresolved():
    s = _store()
    r = context_compile(s, query="the and for this", budget=512, repo_id="r")   # stopwords only
    assert not r.ok and r.anchor_kind == "unresolved" and r.read is None
