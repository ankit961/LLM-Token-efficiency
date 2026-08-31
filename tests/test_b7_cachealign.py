"""B7 — cache model, aligned scheduler, and the gateway's persistent cache-aligned path."""
import json

from contextruntime.cachealign import CacheAlignedScheduler, FireDecision
from contextruntime.cachemodel import CallRecord, PrefixCacheSim, calibrate_append_only
from contextruntime.gateway import RetirementGateway, thinking_gc_upto


def test_prefix_cache_sim_append_only_telescopes():
    sim = PrefixCacheSim()
    r1, w1 = sim.request(0.0, 1000)
    r2, w2 = sim.request(1.0, 1400)
    r3, w3 = sim.request(2.0, 1450)
    assert (r1, w1) == (0, 1000)
    assert (r2, w2) == (1000, 400)
    assert (r3, w3) == (1400, 50)


def test_prefix_cache_sim_edit_serves_partial_prefix():
    """Live-calibrated semantics: an edit at depth X still reads the first X tokens from the
    cached extent (interior hit), re-creates only the suffix, and drops stale larger extents."""
    sim = PrefixCacheSim()
    sim.request(0.0, 1000)
    sim.request(1.0, 1400)
    r, w = sim.request(2.0, 1300, unchanged_prefix_tokens=800)   # deep edit, post-edit total 1300
    assert (r, w) == (800, 500)
    r, w = sim.request(3.0, 1350)                                # append-only again
    assert (r, w) == (1300, 50)


def test_prefix_cache_sim_ttl_expiry_and_warm_seed():
    sim = PrefixCacheSim(ttl_s=3600)
    sim.request(0.0, 1000)
    r, w = sim.request(4000.0, 1200)                             # gap > TTL: cache is gone
    assert (r, w) == (0, 1200)
    assert sim.cold(4000.0 + 3601)


def test_calibrate_append_only_exact_on_clean_stream():
    calls = [CallRecord(P=1000, read=700, creation=300, input=0, out=10, ts=0.0),
             CallRecord(P=1300, read=1000, creation=300, input=0, out=10, ts=1.0),
             CallRecord(P=1500, read=1300, creation=200, input=0, out=10, ts=2.0)]
    c = calibrate_append_only(calls)                             # warm seed = read_1 = 700
    assert c["read_err_pct"] == 0.0 and c["creation_err_pct"] == 0.0


def test_scheduler_cold_gap_and_gate():
    s = CacheAlignedScheduler(mode="gated", ttl_s=3600)
    d = s.decide([], 0, now_ts=100.0)
    assert d.fire and d.reason == "cold-start"
    d = s.decide([("a", 1, 500)], 100_000, now_ts=101.0)         # hot, tiny pending, huge suffix
    assert not d.fire and d.reason == "hold"
    d = s.decide([("a", 1, 50_000)], 1_000, now_ts=102.0)        # 0.1*50k*8 = 40k >= 1.9*1k
    assert d.fire and d.reason == "break-even"
    d = s.decide([("b", 2, 10)], 10, now_ts=102.0 + 3601)        # idle gap beyond TTL
    assert d.fire and d.reason == "ttl-gap"
    s.commit(["a"], 12)
    assert "a" in s.fired_keys and s.strip_frontier == 11
    cold = CacheAlignedScheduler(mode="cold")
    cold.decide([], 0, now_ts=0.0)
    d = cold.decide([("a", 1, 10**9)], 1, now_ts=1.0)            # cold mode never uses the gate
    assert not d.fire


def test_thinking_gc_upto_respects_frontier():
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"u{i}"}]})
        msgs.append({"role": "assistant", "content": [
            {"type": "thinking", "thinking": "...", "signature": "s" * 8},
            {"type": "text", "text": f"a{i}"}]})
    n, _ = thinking_gc_upto(msgs, 2)
    assert n == 2
    kinds = [[b["type"] for b in m["content"]] for m in msgs if m["role"] == "assistant"]
    assert kinds == [["text"], ["text"], ["thinking", "text"]]   # frontier=2 strips first two only


def _history_with_superseded_object(filler_turns=0):
    """Read the same file twice (supersession) + optional filler turns; big first result."""
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "task"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                           "input": {"file_path": "/w/a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "X" * 4000}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Read",
                                           "input": {"file_path": "/w/a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2",
                                      "content": "X" * 4000 + "y"}]},
    ]
    for i in range(filler_turns):
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"step {i}"}]})
        msgs.append({"role": "user", "content": [{"type": "text", "text": "go on"}]})
    msgs.append({"role": "assistant", "content": [{"type": "text", "text": "final"}]})
    msgs.append({"role": "user", "content": [{"type": "text", "text": "continue"}]})
    return msgs


def test_gateway_cache_aligned_fire_then_persist(tmp_path):
    gw = RetirementGateway(mode="enforce", lag=0, batch_turns=10**6,   # boundaries never trigger
                           align="gated", thinking_keep=1,
                           log_path=str(tmp_path / "gw.jsonl"))
    body1 = {"messages": _history_with_superseded_object()}
    _, d1 = gw.process(body1)
    # first request = cold start; the superseded 4000-char result fires and is stubbed
    assert d1.align == "gated" and d1.fired and d1.fire_reason == "cold-start"
    assert d1.applied == 2 and d1.persistent_applied == 0   # superseded + cold-tail (lag=0)
    stub = body1["messages"][2]["content"][0]["content"]
    assert "retired" in stub
    # same history arrives again (client resends full, unmutated): persistent re-apply, no new fire
    body2 = {"messages": _history_with_superseded_object()}
    _, d2 = gw.process(body2)
    assert d2.persistent_applied == 2 and d2.applied == 0 and not d2.fired
    assert "retired" in body2["messages"][2]["content"][0]["content"]
    log = [json.loads(l) for l in open(tmp_path / "gw.jsonl")]
    assert log[0]["fired"] is True and log[1]["fired"] is False
    assert all("ts" in r and "gap_s" in r for r in log)


def test_gateway_align_off_keeps_b6_behavior():
    gw = RetirementGateway(mode="enforce", lag=0, batch_turns=3, align="off", thinking_keep=1)
    body = {"messages": _history_with_superseded_object()}
    _, d = gw.process(body)
    assert d.align == "off" and d.fire_reason == ""              # legacy path: no scheduler fields
    assert d.is_batch_boundary and d.applied == 2                # boundary math unchanged


def test_replay_cold_gap_fires_free_at_idle_gap():
    """A >TTL idle gap makes the suffix re-create under EVERY policy, so firing pending
    retirements there must cost nothing extra: cold_gap's bite <= native's."""
    from corpus.b7_cache_replay import run_policy
    calls = []
    ts = 0.0
    for t in range(1, 13):
        ts += 5000.0 if t == 7 else 10.0                     # idle gap > 1h before call 7
        P = 20_000 + 1_500 * t
        calls.append(CallRecord(P=P, read=P - 1_500, creation=1_500, input=0, out=200, ts=ts))
    events = [{"eligible": 4, "turn": 2, "tokens": 3_000}]
    think = [0] * 13
    nat = run_policy(calls, events, think, "native")
    cg = run_policy(calls, events, think, "cold_gap")
    assert cg["fires"] >= 1 and cg["retired_tokens"] == 3_000
    assert cg["bite"] <= nat["bite"]                          # free window: no cache damage
    assert cg["sum_P"] < nat["sum_P"]                         # and residency actually drops


def test_proxy_gateway_is_a_process_singleton(monkeypatch):
    """B7 regression: the scheduler's fired set lives on the gateway, so the proxy must reuse ONE
    instance across requests (a fresh instance per POST silently resets alignment state)."""
    import contextruntime.gateway_proxy as gp
    monkeypatch.setattr(gp, "_SCHED", None)
    monkeypatch.setenv("CR_GATEWAY_MODE", "enforce")
    monkeypatch.setenv("CR_GATEWAY_CACHE_ALIGN", "gated")
    a = gp.gateway_singleton()
    b = gp.gateway_singleton()
    assert a is not b and a.scheduler is b.scheduler and b.align == "gated"
    a.scheduler.fired_keys.add("t-x")
    assert "t-x" in gp.gateway_singleton().scheduler.fired_keys
    monkeypatch.setenv("CR_GATEWAY_CACHE_ALIGN", "cold")     # mode switch starts fresh state
    c = gp.gateway_singleton()
    assert c.scheduler is not a.scheduler and not c.scheduler.fired_keys
