"""Phase 2.1 — CodeSymbol graph + resolver correctness.

Exact graph assertions (symbol ids, match_kind, confidence), package-qualified
module identity, scope-aware resolution, and an adversarial ambiguity fixture that
must NOT be resolved to an arbitrary module.
"""
import json
from pathlib import Path

import pytest

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


def test_inferred_dependency_is_soft_not_hard():
    """`inferred` = single-candidate guess -> DEPENDS_ON marked soft (never mandatory);
    `exact`/`scoped` are hard (no soft flag)."""
    s, _ = _index(AMBIG, "ambig")
    rows = list(s.conn.execute(
        "SELECT dst_id, match_kind, props FROM code_edges WHERE edge_type='DEPENDS_ON'"))
    by_kind = {r["match_kind"]: r for r in rows}
    # log_event is uniquely named across the repo -> inferred + soft
    inf = next(r for r in rows if r["match_kind"] == "inferred")
    assert inf["dst_id"].endswith("shared.logging.log_event")
    assert json.loads(inf["props"]).get("soft") is True
    # no ambiguous/unresolved ever becomes a dependency
    assert "ambiguous" not in by_kind and "unresolved" not in by_kind
    s.close()


# --- tree-sitter grammar activation (fix #2) ---------------------------------

@pytest.mark.parametrize("lang,grammar", [
    ("javascript", "tree_sitter_javascript"),
    ("typescript", "tree_sitter_typescript"),
    ("go", "tree_sitter_go"),
    ("java", "tree_sitter_java"),
    ("rust", "tree_sitter_rust"),
])
def test_grammar_activates_when_installed(lang, grammar):
    """If the grammar is installed, the registry MUST select tree-sitter — not
    silently degrade to the regex heuristic (this is what caught TypeScript)."""
    pytest.importorskip(grammar)
    from contextruntime.codegraph.adapters import TreeSitterAdapter, TreeSitterUnavailable
    try:
        a = TreeSitterAdapter(lang)
    except TreeSitterUnavailable as e:
        pytest.fail(f"{grammar} installed but tree-sitter did not activate: {e}")
    assert a.parser == "tree_sitter"


# --- nested-definition call scoping (fix #3) ---------------------------------

def test_nested_function_calls_are_scoped_correctly():
    pytest.importorskip("tree_sitter_javascript")
    from contextruntime.codegraph.adapters import TreeSitterAdapter, TreeSitterUnavailable
    try:
        adapter = TreeSitterAdapter("javascript")
    except TreeSitterUnavailable:
        pytest.skip("tree-sitter javascript unavailable")
    src = (Path(__file__).parent / "fixtures" / "nested_js" / "mod.js").read_text()
    _syms, edges = adapter.parse("mod.js", src, "mod")
    calls = {(e.src_qname, e.dst_name) for e in edges if e.edge_type == "CALLS"}
    assert ("mod.outer.inner", "charge") in calls        # charge belongs to inner
    assert ("mod.outer", "inner") in calls               # inner() belongs to outer
    assert ("mod.outer", "charge") not in calls          # NOT misattributed to outer


def test_python_nested_function_calls_are_scoped_correctly():
    # Same invariant as tree-sitter, now enforced for the Python ast adapter: a call
    # inside a nested function belongs to that function, and the nested function is
    # emitted as its own symbol instead of vanishing into its parent.
    src = (
        "def outer():\n"
        "    def inner():\n"
        "        charge()\n"        # belongs to inner, not outer
        "    if True:\n"
        "        def branchy():\n"  # nested inside control flow — still emitted
        "            log()\n"
        "    inner()\n"             # belongs to outer
    )
    syms, edges = PythonAstAdapter().parse("mod.py", src, "mod")
    names = {s.qualified_name for s in syms}
    assert "mod.outer.inner" in names                     # nested fn is its own symbol
    assert "mod.outer.branchy" in names                   # even nested under `if`
    calls = {(e.src_qname, e.dst_name) for e in edges if e.edge_type == "CALLS"}
    assert ("mod.outer.inner", "charge") in calls         # charge belongs to inner
    assert ("mod.outer.branchy", "log") in calls          # log belongs to branchy
    assert ("mod.outer", "inner") in calls                # inner() belongs to outer
    assert ("mod.outer", "charge") not in calls           # NOT misattributed to outer
    assert ("mod.outer", "log") not in calls              # NOT misattributed to outer


def test_python_defs_under_control_flow_are_emitted_at_all_scopes():
    # Definitions hidden inside module-level and class-body control flow must still be
    # emitted with correctly-scoped calls — not just those nested inside a function.
    src = (
        "import sys\n"
        "try:\n"
        "    def load():\n"
        "        _fast()\n"
        "except ImportError:\n"
        "    def load2():\n"
        "        _slow()\n"
        "class Plugin:\n"
        "    if sys.version_info:\n"
        "        def run(self):\n"
        "            go()\n"
    )
    syms, edges = PythonAstAdapter().parse("m.py", src, "m")
    names = {s.qualified_name for s in syms}
    assert {"m.load", "m.load2", "m.Plugin", "m.Plugin.run"} <= names   # none dropped
    calls = {(e.src_qname, e.dst_name) for e in edges if e.edge_type == "CALLS"}
    assert ("m.load", "_fast") in calls
    assert ("m.load2", "_slow") in calls
    assert ("m.Plugin.run", "go") in calls
    contains = {(e.src_qname, e.dst_name) for e in edges if e.edge_type == "CONTAINS"}
    assert ("m.Plugin", "m.Plugin.run") in contains                    # method under class-body `if`


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
