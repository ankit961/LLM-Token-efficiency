"""B4 Context-GC — RetirementPlanner policy + HistoryMutator mechanisms."""
from contextruntime.retirement import (HistoryMutationPlan, InProcessMessageMutator, ObservedObject,
                                       RetirementPlanner, UnsupportedMutator, simulate)


def _obj(oid, turn, key, tok=100, ref=""):
    return ObservedObject(oid, turn, key, tok, ref or f"result://{oid}")


def test_supersession_marks_earlier_same_key_dead():
    p = RetirementPlanner(lag=5, batch_turns=10)
    p.observe(_obj("a", 1, "path:a.py"))
    p.observe(_obj("b", 2, "path:b.py"))
    p.observe(_obj("a2", 3, "path:a.py"))          # re-read a.py supersedes 'a'
    plan = p.plan(6, force=True)                     # force a flush at turn 6
    reasons = {r.obj_id: r.reason for r in plan.retirements}
    assert reasons == {"a": "superseded"}           # only the superseded read; a2/b still warm
    assert plan.retirements[0].recovery_ref == "result://a"


def test_cold_tail_retires_only_after_lag():
    p = RetirementPlanner(lag=5, batch_turns=1)
    p.observe(_obj("a", 1, "path:a.py"))
    assert p.plan(4, force=True).is_empty()         # turn 4: a.py last touched t1, 1+5=6 > 4 -> warm
    plan = p.plan(6, force=True)                     # turn 6: 1+5=6 <= 6 -> cold
    assert [r.obj_id for r in plan.retirements] == ["a"] and plan.retirements[0].reason == "cold_tail"


def test_warm_key_keeps_its_latest_object():
    p = RetirementPlanner(lag=5, batch_turns=1)
    p.observe(_obj("a", 1, "path:a.py"))
    p.observe(_obj("a2", 3, "path:a.py"))
    plan = p.plan(6, force=True)                     # a superseded (retired); a2 is latest, key cold at 6? 3+5=8>6 warm
    ids = {r.obj_id for r in plan.retirements}
    assert ids == {"a"}                              # a2 kept while key a.py is still warm


def test_batching_defers_until_boundary_and_is_idempotent():
    p = RetirementPlanner(lag=5, batch_turns=5)
    p.observe(_obj("a", 1, "path:a.py"))
    p.observe(_obj("a2", 3, "path:a.py"))           # supersedes a immediately
    assert p.plan(4).is_empty()                      # not a batch boundary yet (4 < 5)
    first = p.plan(5)                                # boundary: a superseded; a2 warm (3+5=8 > 5)
    assert [r.obj_id for r in first.retirements] == ["a"]
    later = p.plan(10)                               # next boundary: a NOT re-emitted; a2 now cold (8 <= 10)
    assert [r.obj_id for r in later.retirements] == ["a2"]


def test_touch_keeps_a_key_warm():
    p = RetirementPlanner(lag=5, batch_turns=1)
    p.observe(_obj("a", 1, "path:a.py"))
    p.touch("path:a.py", 7)                          # a later reference keeps it warm
    assert p.plan(10, force=True).is_empty()         # 7+5=12 > 10 -> still warm, not retired


def test_inprocess_mutator_stubs_matching_tool_results():
    ret = RetirementPlanner(lag=5, batch_turns=1)
    ret.observe(_obj("a", 1, "path:a.py", tok=500, ref="result://a"))
    ret.observe(_obj("a2", 3, "path:a.py"))
    plan = ret.plan(6, force=True)
    history = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "big a.py body"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a2", "content": "new a.py body"}]},
    ]
    res = InProcessMessageMutator().apply(plan, history)
    assert res.applied == 1 and res.tokens_freed == 500
    assert history[0]["content"][0]["content"].startswith("[Context note:")
    assert "result://a" in history[0]["content"][0]["content"]
    assert history[1]["content"][0]["content"] == "new a.py body"   # a2 untouched


def test_unsupported_mutator_reports_and_noops():
    m = UnsupportedMutator()
    assert m.supported is False and m.backend == "claude-code-subscription"
    res = m.apply(HistoryMutationPlan(10, (), 0), None)
    assert res.applied == 0 and "unsupported" in res.note


def test_simulate_reproduces_supersession_and_tail():
    objs = [_obj("a", 1, "path:a.py"), _obj("b", 2, "path:b.py"), _obj("a2", 5, "path:a.py")]
    out = simulate(objs, total_turns=20, lag=5, batch_turns=10)
    assert out["n_retired"] == 3                     # a (superseded), b (cold), a2 (cold by end)
    assert out["tokturns_freed"] > 0
