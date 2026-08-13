"""Phase 2.2 — budgeted bundle generator. The required safety/quality properties.

Fixture graph (bundle_repo): service.process (root) ->
  validate (scoped, d1), run_db (scoped, d1) -> q1..q5 (scoped, d2, a DOMINANT branch),
  charge (inferred/soft, d1), save (ambiguous -> hint, no dependency).
"""
import json
import time
from pathlib import Path

from contextruntime.codegraph import builder, bundle
from contextruntime.codegraph.bundle import LEVELS, build_bundle
from contextruntime.model import CodeSymbol
from contextruntime.store import GraphStore

REPO = Path(__file__).parent / "fixtures" / "bundle_repo"


def _store():
    s = GraphStore(":memory:")
    builder.index_path(s, str(REPO), "bundle")
    return s


def _root(s):
    return s.find_symbol("service.process", "bundle")["symbol_id"]


def _levels(b):
    return {x.qualified_name: LEVELS.index(x.level) for x in b.selected}


# 1. budget invariant
def test_used_never_exceeds_budget():
    s = _store(); root = _root(s)
    for B in (40, 70, 100, 150, 200, 500, 1000):
        assert build_bundle(s, root, budget=B).used <= B
    s.close()


# 2. root preservation + explicit insufficiency
def test_root_always_present_or_insufficient():
    s = _store(); root = _root(s)
    big = build_bundle(s, root, budget=1000)
    assert any(x.reason == "root" for x in big.selected)
    tiny = build_bundle(s, root, budget=5)             # can't even seat root
    assert tiny.budget_status == "insufficient" and tiny.minimum_viable_budget > 5
    s.close()


# 3. soft never mandatory
def test_soft_dependency_never_mandatory():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=1000)
    for x in b.selected:
        if x.soft:
            assert x.reason not in ("root", "required-core")   # discretionary only
    # charge is the soft dep and it IS present (as discretionary)
    assert any(x.qualified_name.endswith("charge") and x.soft for x in b.selected)
    s.close()


# 4. ambiguity never becomes a dependency
def test_ambiguous_call_is_hint_not_dependency():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=1000)
    assert not any(x.qualified_name.endswith(".save") for x in b.selected)
    assert any(h["name"] == "save" for h in b.ambiguity_hints)
    # the hint lists both candidates, doesn't pick one
    save_hint = next(h for h in b.ambiguity_hints if h["name"] == "save")
    assert len(save_hint["candidates"]) == 2
    s.close()


# 5. monotonicity: more budget -> never less information
def test_monotonic_in_budget():
    s = _store(); root = _root(s)
    prev = {}
    for B in (70, 100, 130, 160, 200, 300, 600):
        lv = _levels(build_bundle(s, root, budget=B))
        for sym, idx in prev.items():
            assert sym in lv and lv[sym] >= idx           # never dropped or downgraded
        prev = lv
    s.close()


# 6. determinism
def test_deterministic():
    s = _store(); root = _root(s)
    a = json.dumps(build_bundle(s, root, budget=180).to_dict(), sort_keys=True)
    b = json.dumps(build_bundle(s, root, budget=180).to_dict(), sort_keys=True)
    assert a == b
    s.close()


# 7. diversity: a dominant branch cannot monopolize the budget
def test_dominant_branch_does_not_monopolize():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=200)
    names = {x.qualified_name.split(".")[-1] for x in b.selected}
    assert "validate" in names and "charge" in names      # non-DB branches represented
    assert b.metrics["branch_concentration"] < 1.0
    s.close()


# 8. representation preference: a dep appears as signature rather than being dropped
def test_signature_preferred_over_omission_under_pressure():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=130)
    sigs = [x for x in b.selected if x.level == "signature" and x.reason != "root"]
    assert sigs                                            # something is kept at signature
    s.close()


# 9. exact budget boundary (no off-by-one)
def test_exact_boundary_no_overflow():
    s = _store(); root = _root(s)
    # sweep tight budgets; used must always be <= budget with equality allowed
    for B in range(50, 210, 7):
        b = build_bundle(s, root, budget=B)
        assert b.used <= B
    s.close()


# 10. adversarial huge fan-out stays bounded and fast
def test_huge_fanout_bounded_and_fast():
    s = GraphStore(":memory:")
    repo = "big"
    s.put_symbol(CodeSymbol("big::m.py::root", repo, "python", "function", "root",
                            "m.py", 1, 40, "root()", "h", "python_ast", 0.95))
    for i in range(400):
        sid = f"big::m.py::dep{i}"
        s.put_symbol(CodeSymbol(sid, repo, "python", "function", f"dep{i}", "m.py",
                                1, 30, f"dep{i}(a, b)", f"h{i}", "python_ast", 0.95))
        s.add_code_edge(repo, "big::m.py::root", sid, "CALLS", 0.75, "python_ast",
                        match_kind="exact")
    s.commit()
    t = time.time()
    b = build_bundle(s, "big::m.py::root", budget=2000)
    assert time.time() - t < 1.0
    assert b.used <= 2000
    assert len(b.selected) < 402                           # bounded by budget, not fan-out
    s.close()


# 11. no epistemic escalation: a soft relation stays soft at any budget
def test_soft_flag_independent_of_budget():
    s = _store(); root = _root(s)
    for B in (120, 200, 400, 1000):
        b = build_bundle(s, root, budget=B)
        charge = next((x for x in b.selected if x.qualified_name.endswith("charge")), None)
        if charge:
            assert charge.soft is True and charge.match == "inferred"
    s.close()
