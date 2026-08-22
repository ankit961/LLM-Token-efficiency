"""B3.1 staleness-safety replay — D0 re-reference / D1 content-reuse break signals + lag behaviour."""
import json

from corpus.b3_staleness_safety import _distinctive_lines, parse_for_safety, staleness_safety

_LINE = "def compute_the_answer(x): return x + 42000"      # distinctive (>=20 non-space chars)


def _obj(turn, path, dlines=frozenset(), size=500, name="Read"):
    return {"turn": turn, "name": name, "key": "path:" + path, "path": path, "size": size,
            "dlines": dlines}


def test_d0_reference_makes_retirement_unsafe_until_lag_clears_it():
    # a.py read once (tail@2); a later bash at turn 8 cats a.py again (D0 re-reference)
    objs = [_obj(2, "a.py")]
    inputs = {8: json.dumps({"command": "cat a.py"})}
    near = staleness_safety([dict(o) for o in objs], [], inputs, 20, lag=0)
    far = staleness_safety([dict(o) for o in objs], [], inputs, 20, lag=10)
    assert near["n_tail_retired"] == 1 and near["n_unsafe_d0_reref"] == 1 and near["safe_fraction"] == 0.0
    # lag 10 ⇒ retire at turn 12, the turn-8 reference is now BEFORE retirement ⇒ safe
    assert far["n_unsafe_d0_reref"] == 0 and far["safe_fraction"] == 1.0


def test_d1_content_reuse_makes_retirement_unsafe():
    objs = [_obj(2, "a.py", dlines=_distinctive_lines(_LINE))]
    edits = [(9, "b.py", f"    {_LINE}\n    return None")]     # later edit reuses a.py's line
    s = staleness_safety([dict(o) for o in objs], edits, {}, 20, lag=0)
    assert s["n_unsafe_d1_reuse"] == 1 and s["safe_fraction"] == 0.0


def test_untouched_tail_is_safe_and_superseded_always_counted():
    # o1 (a.py@2) superseded by o2 (a.py@5); o3 (b.py@3) is an untouched tail, never re-referenced
    objs = [_obj(2, "a.py"), _obj(5, "a.py"), _obj(3, "b.py")]
    s = staleness_safety([dict(o) for o in objs], [], {}, 20, lag=0)
    assert s["superseded_gross"] == 500 * (20 - 5)            # o1 retired at o2's turn 5
    assert s["n_tail_retired"] == 2 and s["n_safe"] == 2      # a.py@5 tail + b.py@3 tail, both safe
    assert s["safe_fraction"] == 1.0


def test_larger_lag_never_lowers_safe_fraction():
    objs = [_obj(2, "a.py"), _obj(4, "c.py")]
    inputs = {8: json.dumps({"file_path": "a.py"})}
    fracs = [staleness_safety([dict(o) for o in objs], [], inputs, 30, lag=L)["safe_fraction"]
             for L in (0, 5, 10, 20)]
    assert fracs == sorted(fracs)                             # monotonic non-decreasing in lag


def test_parse_for_safety_captures_objects_edits_inputs(tmp_path):
    tp = tmp_path / "t.jsonl"
    rows = [
        {"type": "assistant", "message": {"usage": {"cache_read_input_tokens": 40,
         "cache_creation_input_tokens": 5, "input_tokens": 1, "output_tokens": 1},
         "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1",
         "content": _LINE + "\n"}]}},
        {"type": "assistant", "message": {"usage": {"cache_read_input_tokens": 60,
         "cache_creation_input_tokens": 2, "input_tokens": 1, "output_tokens": 1},
         "content": [{"type": "tool_use", "id": "e1", "name": "Edit",
                      "input": {"file_path": "a.py", "old_string": _LINE}}]}},
    ]
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    objs, edits, inputs, T, usage = parse_for_safety(str(tp))
    assert T == 2 and len(objs) == 1 and objs[0]["path"] == "a.py"
    assert _LINE in objs[0]["dlines"]
    assert edits == [(2, "a.py", _LINE)] and "a.py" in inputs[2]
