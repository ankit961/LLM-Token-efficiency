"""B5.2 Stage A — exact joint replay: hand-computable fixture + super-multiplicative composition."""
import json

from corpus.joint_stack_replay import aggregate, run_stage_a, session_joint

LINE = "def compute_widget_totals(x): return x.alpha + x.beta + 42_000"


def _u(cr, cc, out):
    return {"cache_read_input_tokens": cr, "cache_creation_input_tokens": cc, "input_tokens": 0,
            "output_tokens": out}


def _asst(rid, usage, blocks):
    return {"type": "assistant", "requestId": rid, "message": {"usage": usage, "content": blocks}}


def _res(tuid, content):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tuid, "content": content}]}}


def _fixture(tmp_path):
    """4 calls, P = [100_000, 101_000, 102_000, 103_000], out = 100 each (all thinking: no visible text).
    Calls 1-2 are a D0 discovery run (call 2 reads the path printed by call 1) whose next action edits
    the line served by call 2 ⇒ evidence-gated ⇒ call 2 avoided. Call 1's read of a.py is superseded by
    nothing; b.py read is the edit's source (kept). No B3 retirements fire within T (lag 5, T=4)."""
    rows = [
        {"type": "user", "message": {"content": "fix the widget bug"}},
        _asst("r1", _u(100_000, 0, 100), [{"type": "tool_use", "id": "t1", "name": "Bash",
                                          "input": {"command": "grep -rn compute_widget_totals src/"}}]),
        _res("t1", "src/b.py:12: def compute_widget_totals"),
        _asst("r2", _u(101_000, 0, 100), [{"type": "tool_use", "id": "t2", "name": "Read",
                                          "input": {"file_path": "src/b.py"}}]),
        _res("t2", f"  12\t{LINE}\n"),
        _asst("r3", _u(102_000, 0, 100), [{"type": "tool_use", "id": "t3", "name": "Edit",
                                          "input": {"file_path": "src/b.py", "old_string": LINE, "new_string": "x"}}]),
        _res("t3", "edit ok"),
        _asst("r4", _u(103_000, 0, 100), [{"type": "tool_use", "id": "t4", "name": "Bash",
                                          "input": {"command": "python -m pytest tests/"}}]),
        _res("t4", "1 passed"),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


def test_exact_levels_hand_computed(tmp_path):
    tp = _fixture(tmp_path)
    r = session_joint(tp, prefix_frac=0.10)             # H = 10% of startup P (100_000) = 10_000
    assert r["N"] == 4 and r["avoided"] == 1            # call 2 (the D0 follow-up read) collapses
    L = r["levels"]
    assert L["L0"]["P"] == 100_000 + 101_000 + 102_000 + 103_000
    assert L["L1"]["P"] == L["L0"]["P"] - 4 * 10_000    # hygiene: −H on every call
    assert L["L2"]["P"] == L["L1"]["P"] - (101_000 - 10_000)   # call 2 dropped at its hygiene-reduced size
    assert L["L2"]["calls"] == 3
    # B3 correctly retires the b.py READ once the Edit supersedes it (post-edit stale) at call 3,
    # so call 4 sheds that small read's size; no other retirement fits inside T=4 (lag 5)
    b3_delta = L["L2"]["P"] - L["L3"]["P"]
    assert 0 < b3_delta < 100
    # thinking (keep-1): at call t the strippable set is calls 1..t-2. Kept calls are 1,3,4 with
    # cumulative think 0, think_1, think_1+think_2 — compute expected from the same measurement.
    from corpus.joint_stack_replay import visible_out_per_call
    from corpus.call_collapse_oracle import parse_session
    calls = parse_session(tp)
    vis = visible_out_per_call(tp)
    think = [max(c["out_tokens"] - round(1.74 * v), 0) for c, v in zip(calls, vis)]
    assert L["L4"]["P"] == L["L3"]["P"] - (2 * think[0] + think[1])
    assert r["think_total_measured"] == sum(think)
    # solo collapse = ΣP minus call 2 at FULL size (no hygiene interaction in the solo lever)
    assert r["solo"]["collapse"] == L["L0"]["P"] - 101_000
    assert r["solo"]["prefix"] == L["L1"]["P"]


def test_composition_is_super_multiplicative(tmp_path):
    """Disjoint absolute slices compose closer to additive-in-tokens than multiplicative-in-fractions:
    exact joint ≥ the multiplicative approximation built from the same solo levers."""
    tp = _fixture(tmp_path)
    agg = aggregate([session_joint(tp, prefix_frac=0.10)], "t")
    assert agg["exact_joint_pct"] >= agg["multiplicative_approx_pct"]
    assert agg["approx_minus_exact_pp"] <= 0
    assert agg["clamped_calls"] == 0 and agg["b3_turn_axis_mismatches"] == 0


def test_run_stage_a_over_results_json(tmp_path):
    tp = _fixture(tmp_path)
    res = {"task|arm|rep0": {"transcript": tp, "num_turns": 4}}
    rp = tmp_path / "res.json"
    rp.write_text(json.dumps(res))
    out = run_stage_a(str(rp), prefix_frac=0.10, label="fixture")
    assert out["aggregate"]["sessions"] == 1
    assert out["aggregate"]["L0"]["calls"] == 4 and out["aggregate"]["L4"]["calls"] == 3


def test_v2_defer_schedule_and_collapsed_timeline(tmp_path):
    """v2: per-tool deferral reduces the prefix only BEFORE a tool's first use; B3 runs on the
    collapsed timeline; thinking counts kept calls only (output-side factor)."""
    from corpus.joint_stack_replay import session_joint_v2, FACTOR_OUT
    from corpus.joint_stack_replay import visible_out_per_call
    from corpus.call_collapse_oracle import parse_session
    tp = _fixture(tmp_path)
    r = session_joint_v2(tp, sub_frac=0.10, deferrable_sizes={"Read": 5000})
    L = r["levels"]
    # Read first used at call 2 -> its 5k schema is deferred ONLY on call 1 (H=10k on every call)
    assert L["L1"]["P"] == L["L0"]["P"] - 4 * 10_000 - 5_000
    # collapsed timeline: call 2 avoided; kept 1,3,4 -> new 1,2,3. The b.py read (mapped to the packet
    # call) is superseded by the Edit (new turn 2) and retires SAFELY -> the last kept call sheds it.
    assert L["L2"]["calls"] == 3
    b3_delta = L["L2"]["P"] - L["L3"]["P"]
    assert 0 < b3_delta < 100
    # thinking: kept calls only, keep-1 on the NEW sequence -> only think_kept[0] applies (at new t=3)
    calls = parse_session(tp)
    vis = visible_out_per_call(tp)
    tk = [max(calls[t - 1]["out_tokens"] - round(FACTOR_OUT * vis[t - 1]), 0) for t in (1, 3, 4)]
    assert L["L4"]["P"] == L["L3"]["P"] - tk[0]
    assert r["think_total_measured"] == sum(tk)          # avoided call 2's thinking NOT counted here
