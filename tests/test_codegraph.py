"""Phase 2.1 — CodeSymbol graph + resolver correctness.

Exact graph assertions (symbol ids, match_kind, confidence), package-qualified
module identity, scope-aware resolution, and an adversarial ambiguity fixture that
must NOT be resolved to an arbitrary module.
"""
from pathlib import Path

from contextruntime.codegraph import builder
from contextruntime.codegraph.adapters import HeuristicAdapter, PythonAstAdapter
from contextruntime.codegraph.builder import module_qname
from contextruntime.store import GraphStore

SAMPLE = Path(__file__).parent / "fixtures" / "sample_py"
AMBIG = Path(__file__).parent / "fixtures" / "ambig_repo"


def _index(path, repo):
    s = GraphStore(":memory:")
    rep = builder.index_path(s, str(path), repo_id=repo)
    return s, rep


# --- module identity (fix #2) ------------------------------------------------

def test_module_qname_is_package_qualified():
    assert module_qname("payments/utils.py") == "payments.utils"
    assert module_qname("users/utils.py") == "users.utils"       # not the same as above
    assert module_qname("src/payments/service.py") == "payments.service"
    assert module_qname("payments/__init__.py") == "payments"


# --- adapters ----------------------------------------------------------------

def test_python_ast_extracts_scoped_structure():
    src = (SAMPLE / "payment.py").read_text()
    syms, edges = PythonAstAdapter().parse("payment.py", src, "payment")
    names = {s.qualified_name for s in syms}
    assert {"payment.PaymentService", "payment.PaymentService.process",
            "payment.PaymentService.validate"} <= names
    assert any(s.kind == "test" for s in syms)
    assert any(e.edge_type == "CALLS" and e.dst_name == "validate" for e in edges)
    assert any(e.edge_type == "IMPLEMENTS" and e.dst_name == "Base" for e in edges)


def test_heuristic_is_low_confidence_and_no_calls():
    src = (SAMPLE / "widget.js").read_text()
    syms, edges = HeuristicAdapter("javascript").parse("widget.js", src, "widget")
    assert {"widget.Widget", "widget.mount"} <= {s.qualified_name for s in syms}
    assert all(e.resolution == "regex_heuristic" for e in edges)
    assert not any(e.edge_type == "CALLS" for e in edges)   # heuristic doesn't guess calls


# --- resolution (fix #1, #7) -------------------------------------------------

def test_scoped_resolution_hits_sibling_method():
    s, _ = _index(SAMPLE, "sample")
    row = s.conn.execute(
        "SELECT dst_id, match_kind FROM code_edges "
        "WHERE edge_type='CALLS' AND src_id LIKE '%PaymentService.process' "
        "AND dst_id LIKE '%validate'").fetchone()
    assert row is not None
    assert row["match_kind"] == "scoped"
    assert row["dst_id"].endswith("payment.PaymentService.validate")
    s.close()


def test_ambiguous_call_is_not_falsely_resolved():
    """The core safety property: save() with two candidates must NOT pick one."""
    s, rep = _index(AMBIG, "ambig")
    call = s.conn.execute(
        "SELECT dst_id, match_kind, ambiguity_count FROM code_edges "
        "WHERE edge_type='CALLS' AND dst_id LIKE '%save%'").fetchone()
    assert call is not None
    assert call["match_kind"] == "ambiguous"
    assert call["ambiguity_count"] == 2
    assert call["dst_id"] == "ambiguous:save"
    # and crucially: no DEPENDS_ON invented to either concrete save symbol
    bad = s.conn.execute(
        "SELECT COUNT(*) c FROM code_edges WHERE edge_type='DEPENDS_ON' "
        "AND dst_id LIKE '%repo.save'").fetchone()["c"]
    assert bad == 0
    assert rep.match_by_kind.get("ambiguous", 0) >= 1
    s.close()


def test_depends_on_only_from_dependable_matches():
    s, _ = _index(SAMPLE, "sample")
    kinds = {r["match_kind"] for r in
             s.conn.execute("SELECT match_kind FROM code_edges WHERE edge_type='DEPENDS_ON'")}
    assert kinds <= {"exact", "scoped", "inferred"}     # never ambiguous/unresolved
    s.close()


def test_index_report_and_confidence_invariant():
    s, rep = _index(SAMPLE, "sample")
    assert rep.files == 2
    langs = {r["language"] for r in s.conn.execute("SELECT DISTINCT language FROM symbols")}
    assert {"python", "javascript"} <= langs
    assert "structural_confidence_by_language" in rep.__dict__
    contains = [r["confidence"] for r in
                s.conn.execute("SELECT confidence FROM code_edges WHERE edge_type='CONTAINS'")]
    calls = [r["confidence"] for r in
             s.conn.execute("SELECT confidence FROM code_edges WHERE edge_type='CALLS'")]
    assert max(contains) > max(calls)                   # structural > call resolution
    s.close()


def test_reindex_is_idempotent():
    s, _ = _index(SAMPLE, "sample")
    n1 = (s.count("symbols"),
          s.conn.execute("SELECT COUNT(*) c FROM code_edges").fetchone()["c"])
    builder.index_path(s, str(SAMPLE), repo_id="sample")
    n2 = (s.count("symbols"),
          s.conn.execute("SELECT COUNT(*) c FROM code_edges").fetchone()["c"])
    assert n1 == n2
    s.close()
