"""Phase 2.2 / 2.2.1 — budgeted bundle planner. Required safety/quality properties.

Fixture graph (bundle_repo): service.process (root) ->
  validate (scoped, d1), run_db (scoped, d1) -> q1..q5 (scoped, d2, a DOMINANT branch),
  charge (inferred/soft, d1), save (ambiguous -> hint, no dependency).
Mandatory set is ROOT ONLY (hard deps are eligible-for-mandatory, not auto-mandatory).
"""
import json
import time
from pathlib import Path

from contextruntime.codegraph import builder, bundle
from contextruntime.codegraph.bundle import LEVELS, LEVEL_VALUE, BundlePolicy, build_bundle
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


def _level_of(s, root, B, suffix):
    b = build_bundle(s, root, budget=B)
    return next((LEVELS.index(x.level) for x in b.selected
                 if x.qualified_name.endswith(suffix)), -1)


# 1. budget invariant + 9. exact boundary (no off-by-one)
def test_used_never_exceeds_budget():
    s = _store(); root = _root(s)
    for B in list(range(48, 220, 6)) + [500, 1000]:
        assert build_bundle(s, root, budget=B).used <= B
    s.close()


# 2. root preservation + explicit insufficiency (minimum = root only now)
def test_root_present_or_insufficient():
    s = _store(); root = _root(s)
    big = build_bundle(s, root, budget=1000)
    assert sum(1 for x in big.selected if x.reason == "root") == 1
    tiny = build_bundle(s, root, budget=5)
    assert tiny.budget_status == "insufficient" and tiny.minimum_viable_budget > 5
    # minimum viable budget is the ROOT alone (no auto-mandated core)
    assert big.minimum_viable_budget == next(x.tokens for x in big.selected if x.reason == "root")
    s.close()


# 3. hard=eligible-not-mandatory; soft never mandatory
def test_mandatory_is_root_only_and_soft_is_discretionary():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=1000)
    assert all(x.reason in ("hard", "soft") for x in b.selected if x.reason != "root")
    assert any(x.qualified_name.endswith("charge") and x.soft and x.reason == "soft"
               for x in b.selected)
    # not every direct hard dep is force-included at tiny budgets
    tiny = build_bundle(s, root, budget=64)          # only fits root + one signature
    assert len([x for x in tiny.selected if x.reason == "hard"]) < b.metrics["hard_candidates"]
    s.close()


# 4. ambiguity is a hint, never a dependency
def test_ambiguous_call_is_hint_not_dependency():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=1000)
    assert not any(x.qualified_name.endswith(".save") for x in b.selected)
    hint = next(h for h in b.ambiguity_hints if h["name"] == "save")
    assert len(hint["candidates"]) == 2
    s.close()


# repo-scoped hints — never leak another repo's symbol names (2.2.1)
def test_ambiguity_hint_is_repo_scoped():
    s = _store()                                     # repo "bundle" has a.save + b.save
    # a second repo with its own uniquely-named 'save'
    other = Path(__file__).parent / "fixtures" / "_hint_other"
    other.mkdir(exist_ok=True)
    (other / "zzz.py").write_text("def save(x):\n    return x\n")
    try:
        builder.index_path(s, str(other), "otherrepo")
        b = build_bundle(s, _root(s), budget=1000)
        hint = next(h for h in b.ambiguity_hints if h["name"] == "save")
        assert set(hint["candidates"]) == {"a.save", "b.save"}      # not zzz.save
        assert "zzz.save" not in hint["candidates"]
    finally:
        (other / "zzz.py").unlink(missing_ok=True); other.rmdir()
    s.close()


# 5. monotonicity: more budget -> never less information
def test_monotonic_in_budget():
    s = _store(); root = _root(s)
    prev = {}
    for B in (62, 70, 100, 120, 160, 200, 600):
        lv = _levels(build_bundle(s, root, budget=B))
        for sym, idx in prev.items():
            assert sym in lv and lv[sym] >= idx
        prev = lv
    s.close()


# 6. determinism
def test_deterministic():
    s = _store(); root = _root(s)
    a = json.dumps(build_bundle(s, root, budget=180).to_dict(), sort_keys=True)
    b = json.dumps(build_bundle(s, root, budget=180).to_dict(), sort_keys=True)
    assert a == b
    s.close()


# 7. diversity: a dominant branch cannot monopolize the discretionary budget
def test_dominant_branch_does_not_monopolize():
    s = _store(); root = _root(s)
    b = build_bundle(s, root, budget=120)
    names = {x.qualified_name.split(".")[-1] for x in b.selected}
    assert {"validate", "charge"} <= names            # non-DB branches represented
    assert b.metrics["branch_concentration"] < 0.5
    s.close()


# 8. representation preference: (a) a dep is kept at a REDUCED level rather than
#    dropped under pressure; (b) the same symbol UPGRADES as budget grows.
def test_representation_upgrades_with_budget():
    s = _store(); root = _root(s)
    tight = build_bundle(s, root, budget=62)
    reduced = [x for x in tight.selected
               if x.reason != "root" and x.level != "implementation"]
    assert reduced                                    # kept at reduced rep, not dropped
    lo = _level_of(s, root, 62, ".q5")                # present at a reduced level
    hi = _level_of(s, root, 70, ".q5")                # upgraded with more budget
    assert 0 <= lo < hi
    assert LEVELS[hi] == "implementation"
    s.close()


# 10. adversarial huge fan-out: a NONTRIVIAL bounded selection (not just insufficient)
def test_huge_fanout_selects_partial_and_fast():
    s = GraphStore(":memory:")
    repo = "big"
    s.put_symbol(CodeSymbol("big::m.py::root", repo, "python", "function", "root",
                            "m.py", 1, 40, "root()", "h", "python_ast", 0.95))
    for i in range(400):
        sid = f"big::m.py::dep{i:03d}"
        s.put_symbol(CodeSymbol(sid, repo, "python", "function", f"dep{i:03d}", "m.py",
                                1, 30, f"dep{i:03d}(a, b)", f"h{i}", "python_ast", 0.95))
        s.add_code_edge(repo, "big::m.py::root", sid, "CALLS", 0.75, "python_ast",
                        match_kind="exact")
    s.commit()
    t = time.time()
    b = build_bundle(s, "big::m.py::root", budget=2000)
    assert time.time() - t < 1.0
    assert b.budget_status == "ok"
    assert b.used <= 2000
    assert 1 < len(b.selected) < 401                  # some deps selected, not all
    assert len(b.excluded) > 0                        # and some excluded by budget
    s.close()


# 11. no epistemic escalation: a soft relation stays soft at any budget
def test_soft_flag_independent_of_budget():
    s = _store(); root = _root(s)
    for B in (120, 200, 400, 1000):
        charge = next((x for x in build_bundle(s, root, budget=B).selected
                       if x.qualified_name.endswith("charge")), None)
        if charge:
            assert charge.soft is True and charge.match == "inferred"
    s.close()


# 2.2.1: the planner is an APPROXIMATION — benchmark it vs an exact solver.
def _optimal_utility(s, root_id, budget, pol):
    """Exact multiple-choice knapsack over discretionary candidates (DP)."""
    root = s.symbol_row(root_id)
    B = budget - bundle._level_tokens(root, pol.root_level, pol)
    if B < 0:
        return 0.0
    cands, _ = bundle._collect(s, root_id, root["repo_id"], pol)
    dp = [0.0] * (B + 1)
    for c in cands.values():
        choices = [(0, 0.0)] + [(bundle._level_tokens(c["row"], lv, pol),
                                 c["base_utility"] * LEVEL_VALUE[lv]) for lv in LEVELS]
        ndp = dp[:]
        for j in range(B + 1):
            for cost, val in choices:
                if cost <= j and dp[j - cost] + val > ndp[j]:
                    ndp[j] = dp[j - cost] + val
        dp = ndp
    return max(dp)


def test_greedy_approximation_ratio():
    s = _store(); root = _root(s); pol = BundlePolicy()
    ratios = []
    for B in (70, 100, 130, 160, 200):
        got = build_bundle(s, root, budget=B).metrics["utility_selected"]
        opt = _optimal_utility(s, root, B, pol)
        if opt > 0:
            ratios.append(got / opt)
    ratios.sort()
    median = ratios[len(ratios) // 2]
    # monotone+diversity greedy should stay close to the exact optimum
    assert median >= 0.85, f"approximation ratios={[round(r,3) for r in ratios]}"
    s.close()
