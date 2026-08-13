"""Phase 2.3 — SemanticFS read surface: materializer (content monotonicity),
read_symbol (real source within RENDERED budget), read_slice, find_callers,
context_search (handles not dumps), context_expand (progressive), PRE metric.
"""
from pathlib import Path

from contextruntime.codegraph import builder
from contextruntime.codegraph.render import render_symbol
from contextruntime.semanticfs import (context_expand, context_search, find_callers,
                                       read_slice, read_symbol)
from contextruntime.store import GraphStore

REPO = Path(__file__).parent / "fixtures" / "bundle_repo"


def _store():
    s = GraphStore(":memory:")
    builder.index_path(s, str(REPO), "bundle")
    return s


def _sid(s, qn):
    return s.find_symbol(qn, "bundle")["symbol_id"]


# content monotonicity: lines(L1) ⊆ lines(L2) ⊆ lines(L3) ⊆ lines(L4)
def test_content_monotonicity():
    s = _store()
    row = s.symbol_row(_sid(s, "service.run_db"))     # a multi-line function
    sets = [render_symbol(s, row, lv).included_lines
            for lv in ("signature", "skeleton", "slice", "implementation")]
    for lo, hi in zip(sets, sets[1:]):
        assert lo <= hi                                # strictly nested
    assert sets[0] < sets[-1]                          # signature is smaller than impl
    s.close()


# read_symbol returns REAL source-derived text, not metadata
def test_read_symbol_returns_real_source():
    s = _store()
    rr = read_symbol(s, "service.process", budget=2048)
    assert rr.ok
    txt = rr.to_text()
    assert "def process" in txt                        # actual code from the fixture
    assert "validate" in txt                           # a real dependency call
    # provenance travels with each section
    root = rr.sections[0]
    assert root["provenance"]["path"].endswith("service.py")
    assert root["provenance"]["content_hash"]
    s.close()


# rendered budget invariant (not merely the planner estimate)
def test_rendered_budget_respected():
    s = _store()
    for B in (60, 120, 300, 1000):
        rr = read_symbol(s, "service.process", budget=B)
        assert rr.budget["rendered_estimate"] <= B     # RENDERED tokens, validated
    s.close()


# shrink validator: a tight budget forces a downgrade but stays within budget
def test_shrink_downgrades_and_fits():
    s = _store()
    big = read_symbol(s, "service.process", budget=1000)
    tight = read_symbol(s, "service.process", budget=90)
    assert tight.budget["rendered_estimate"] <= 90
    # the root is represented at a level no higher than in the roomy bundle
    from contextruntime.semanticfs import DOWNGRADE
    assert DOWNGRADE.index(tight.sections[0]["level"]) <= DOWNGRADE.index(big.sections[0]["level"])
    s.close()


# planned-vs-rendered error is reported
def test_pre_metric_present():
    s = _store()
    rr = read_symbol(s, "service.process", budget=500)
    assert "planned_vs_rendered_error" in rr.budget
    assert rr.budget["estimator"] == "chars4-v1"
    s.close()


# find_callers = reverse CALLS traversal, compact + handles
def test_find_callers():
    s = _store()
    callers = find_callers(s, "service.validate")
    names = {c["qualified_name"] for c in callers}
    assert "service.process" in names                  # process calls validate
    assert all(c["handle"].startswith("ctx://symbol/") for c in callers)
    s.close()


# context_search returns handles, never code dumps
def test_context_search_returns_handles_not_code():
    s = _store()
    hits = context_search(s, "process")
    assert hits and all(h["handle"].startswith("ctx://symbol/") for h in hits)
    assert all("text" not in h and "source" not in h for h in hits)   # no code
    assert any(h["qualified_name"] == "service.process" for h in hits)
    s.close()


# progressive expansion: a ctx://symbol handle resolves to rendered source
def test_context_expand_symbol_handle():
    s = _store()
    sid = _sid(s, "service.run_db")
    exp = context_expand(s, f"ctx://symbol/{sid}")
    assert exp.found and "def run_db" in exp.text
    # level-qualified handle
    sig = context_expand(s, f"ctx://symbol/{sid}@signature")
    assert sig.found and len(sig.text) <= len(exp.text)
    # unknown handle never silently empty
    bad = context_expand(s, "ctx://symbol/nope")
    assert not bad.found
    s.close()


def test_read_slice():
    s = _store()
    rr = read_slice(s, "service.run_db", budget=512)
    assert rr.ok and rr.sections[0]["level"] == "slice"
    s.close()
