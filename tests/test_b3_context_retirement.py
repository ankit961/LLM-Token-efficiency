"""B3 retroactive context-retirement harness — obsolescence, ceiling, and cost/rewrite accounting."""
import json

from corpus.b3_context_retirement import (_obj_key, assign_obsolescence, parse_context_objects,
                                          retirement_ceiling, simulate_batched)

_USAGE = {"cache_read": 100_000, "cache_creation": 2_000, "input": 100, "output": 900, "T_total": 103_000}


def _objs():
    # file A read at turns 1 and 3 (the t1 read is superseded by t3); file B read once at turn 2
    return [{"turn": 1, "name": "Read", "key": "path:a.py", "size": 500},
            {"turn": 3, "name": "Read", "key": "path:a.py", "size": 500},
            {"turn": 2, "name": "Read", "key": "path:b.py", "size": 300}]


def test_obj_key_paths_vs_commands():
    assert _obj_key("Read", {"file_path": "x/y.py"}) == "path:x/y.py"
    assert _obj_key("Edit", {"file_path": "x/y.py"}) == "path:x/y.py"      # edits share the file key
    assert _obj_key("Bash", {"command": " ls -a "}) == "bash:ls -a"
    assert _obj_key("Grep", {"pattern": "foo"}).startswith("grep:")


def test_assign_obsolescence_supersede_and_tail():
    objs = assign_obsolescence(_objs(), 10)
    a1, a3, b2 = objs
    assert a1["obsolete_turn"] == 3 and a1["tail_turn"] is None            # superseded by the turn-3 re-read
    assert a3["obsolete_turn"] is None and a3["tail_turn"] == 3            # last touch of a.py = tail
    assert b2["obsolete_turn"] is None and b2["tail_turn"] == 2            # only touch of b.py = tail


def test_ceiling_tail_dominates_mechanical():
    objs = assign_obsolescence(_objs(), 10)
    mech = retirement_ceiling(objs, 10, _USAGE, include_tail=False)
    full = retirement_ceiling(objs, 10, _USAGE, include_tail=True)
    assert mech["n_retired"] == 1 and full["n_retired"] == 3               # only a1 is provably dead
    # a1 retired at turn 3 for 7 remaining turns: 500*7 = 3500
    assert mech["gross_tokturns"] == 3500
    assert full["gross_tokturns"] > mech["gross_tokturns"]                 # tail adds a3,b2
    assert 0 < mech["pct_of_cache_read"] < full["pct_of_cache_read"]


def test_batched_once_end_is_near_zero_and_raw_ge_cost():
    objs = assign_obsolescence(_objs(), 10)
    once = simulate_batched(objs, 10, _USAGE, policy="once_end", include_tail=True)
    everyk = simulate_batched(objs, 10, _USAGE, policy="everyK", K=1, include_tail=True)
    assert once["realized_gross_tokturns"] < everyk["realized_gross_tokturns"]   # no remaining turns at the end
    assert everyk["raw_net_pct_of_T_total"] >= everyk["cost_net_pct_of_baseline"]  # read cheap ⇒ cost NET ≤ raw NET
    assert everyk["cost_rewrite"] >= 0 and everyk["n_events"] >= 1


def test_batched_coarser_K_fewer_events():
    objs = assign_obsolescence(_objs(), 20)
    fine = simulate_batched(objs, 20, _USAGE, policy="everyK", K=1, include_tail=True)
    coarse = simulate_batched(objs, 20, _USAGE, policy="everyK", K=10, include_tail=True)
    assert coarse["n_events"] <= fine["n_events"]


def test_parse_context_objects_synthetic(tmp_path):
    tp = tmp_path / "t.jsonl"
    rows = [
        {"type": "assistant", "message": {"usage": {"cache_read_input_tokens": 50,
         "cache_creation_input_tokens": 10, "input_tokens": 1, "output_tokens": 2},
         "content": [{"type": "tool_use", "id": "u1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "u1",
         "content": "line one\nline two\n"}]}},
    ]
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    objs, turns, usage = parse_context_objects(str(tp))
    assert turns == 1 and len(objs) == 1
    assert objs[0]["name"] == "Read" and objs[0]["key"] == "path:a.py" and objs[0]["size"] > 0
    assert usage["cache_read"] == 50 and usage["T_total"] == 63
