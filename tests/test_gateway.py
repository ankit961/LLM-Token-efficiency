"""B4 Context-GC gateway adapter — OBSERVE never mutates, kill-switch, enforce-at-boundary, fail-open."""
import copy
import json

from contextruntime.gateway import (RetirementGateway, gateway_mode_from_env, message_objects,
                                     object_key, summarize_log)


def _msgs():
    """a.py read (r1) → b.py read (r2) → a.py re-read (r3, supersedes r1). 3 assistant turns."""
    return [
        {"role": "user", "content": "fix a.py"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r1", "content": "OLD BODY OF A"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r2", "name": "Read", "input": {"file_path": "b.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r2", "content": "body of b"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r3", "name": "Read", "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r3", "content": "new body of a"}]},
    ]


def _result_by_id(messages, tuid):
    for m in messages:
        for b in (m.get("content") if isinstance(m.get("content"), list) else []):
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tuid:
                return b["content"]
    return None


def test_message_objects_maps_turns_and_keys():
    objs, n_turns = message_objects(_msgs())
    assert n_turns == 3
    got = {o.obj_id: (o.turn, o.key) for o in objs}
    assert got == {"r1": (1, "path:a.py"), "r2": (2, "path:b.py"), "r3": (3, "path:a.py")}


def test_object_key_shapes():
    assert object_key("Read", {"file_path": "x/y.py"}) == "path:x/y.py"
    assert object_key("Bash", {"command": " ls "}) == "bash:ls"
    assert object_key("WebFetch", {"url": "u"}) == ""            # not a retirable tool


def test_observe_never_mutates_but_reports_opportunity():
    body = {"messages": _msgs()}
    before = copy.deepcopy(body)
    g = RetirementGateway(mode="observe", lag=5, batch_turns=10)
    out, dec = g.process(body)
    assert out is body and body == before                        # byte-for-byte unchanged
    assert _result_by_id(out["messages"], "r1") == "OLD BODY OF A"
    assert dec.mode == "observe" and dec.n_retirable == 1 and dec.tokens_retirable > 0
    assert dec.applied == 0


def test_off_is_passthrough_no_planning():
    body = {"messages": _msgs()}
    out, dec = RetirementGateway(mode="off").process(body)
    assert out is body and dec is None


def test_enforce_stubs_only_at_batch_boundary():
    # batch_turns=3 → turn 3 is a boundary → the superseded r1 gets stubbed
    body = {"messages": _msgs()}
    out, dec = RetirementGateway(mode="enforce", lag=5, batch_turns=3).process(body)
    assert dec.is_batch_boundary and dec.applied == 1
    assert _result_by_id(out["messages"], "r1").startswith("[Context note:")
    assert _result_by_id(out["messages"], "r3") == "new body of a"   # latest kept

    # batch_turns=10 → turn 3 is NOT a boundary → nothing mutated
    body2 = {"messages": _msgs()}
    out2, dec2 = RetirementGateway(mode="enforce", lag=5, batch_turns=10).process(body2)
    assert not dec2.is_batch_boundary and dec2.applied == 0
    assert _result_by_id(out2["messages"], "r1") == "OLD BODY OF A"


def test_fail_open_on_malformed_body():
    body = {"messages": {"not": "a list"}}                        # iterating raises inside → fail-open
    out, dec = RetirementGateway(mode="observe").process(body)
    assert out is body and dec is None


def test_mode_from_env(monkeypatch):
    monkeypatch.delenv("CR_GATEWAY_MODE", raising=False)
    assert gateway_mode_from_env() == "off"
    monkeypatch.setenv("CR_GATEWAY_MODE", "observe")
    assert gateway_mode_from_env() == "observe"
    monkeypatch.setenv("CR_GATEWAY_MODE", "garbage")
    assert gateway_mode_from_env() == "off"


def test_summarize_log(tmp_path):
    log = tmp_path / "gw.jsonl"
    g = RetirementGateway(mode="observe", lag=5, batch_turns=3, log_path=str(log))
    g.process({"messages": _msgs()})
    g.process({"messages": _msgs()})
    s = summarize_log(str(log))
    assert s["requests"] == 2 and s["max_retirable_tokens"] > 0 and s["peak_turn"] == 3
