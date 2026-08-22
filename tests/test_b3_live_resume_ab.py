"""B3.3 live-resume harness — pure functions (truncation, retirement selection, stub, replay, analysis).
The live `claude -p --resume` path is not exercised here."""
import os

from corpus.b3_live_resume_ab import (analyze_continuation, clean_cut, emit_variant, objects_and_edits,
                                      retired_by, turns_of, STUB, apply_edits)


def _asst(uid, tool, tuid, inp, usage=True):
    m = {"content": [{"type": "tool_use", "id": tuid, "name": tool, "input": inp}]}
    if usage:
        m["usage"] = {"cache_read_input_tokens": 10}
    return {"type": "assistant", "uuid": uid, "message": m}


def _res(uid, tuid, content):
    return {"type": "user", "uuid": uid, "message": {"content": [{"type": "tool_result", "tool_use_id": tuid, "content": content}]}}


def _session():
    return [
        {"type": "user", "uuid": "u0", "message": {"content": "Fix the bug in a.py"}},
        _asst("a1", "Read", "r1", {"file_path": "a.py"}),          # turn 1: read a.py
        _res("u1", "r1", "old body of a.py"),
        _asst("a2", "Read", "r2", {"file_path": "a.py"}),          # turn 2: re-read a.py (supersedes r1)
        _res("u2", "r2", "new body of a.py"),
        _asst("a3", "Edit", "e1", {"file_path": "a.py", "old_string": "x", "new_string": "y"}),  # turn 3
        _res("u3", "e1", "edit ok"),
    ]


def test_turns_and_objects():
    recs = _session()
    turns = turns_of(recs)
    assert max(turns) == 3                                          # three assistant turns
    objs, edits = objects_and_edits(recs, turns)
    assert [o["tuid"] for o in objs] == ["r1", "r2", "e1"]
    assert all(o["key"] == "path:a.py" for o in objs)
    assert edits and edits[0][1] == "Edit"


def test_retired_by_supersession_only_before_cut():
    recs = _session()
    turns = turns_of(recs)
    objs, _ = objects_and_edits(recs, turns)
    tuids, paths = retired_by(objs, tstar=2, total_turns=3)
    assert tuids == {"r1"}                                          # r1 superseded by r2 at turn 2
    assert paths == {"a.py"}
    # at t*=3 the re-read r2 is also superseded (by the edit) -> retirable
    tuids3, _ = retired_by(objs, tstar=3, total_turns=3)
    assert "r2" in tuids3


def test_clean_cut_at_turn_boundary():
    recs = _session()
    turns = turns_of(recs)
    cut = clean_cut(recs, turns, tstar=2)
    assert recs[cut]["uuid"] == "u2"                               # last closed record at turn 2


def test_emit_variant_stubs_only_retired():
    recs = _session()
    kept = recs[:5]                                                # through u2
    stubbed = emit_variant(kept, "sid-new", {"r1"}, stub=True)
    got = {r["uuid"]: r for r in stubbed}
    assert got["u1"]["message"]["content"][0]["content"] == STUB   # r1 retired -> stubbed
    assert got["u2"]["message"]["content"][0]["content"] == "new body of a.py"  # r2 kept
    assert all(r["sessionId"] == "sid-new" for r in stubbed)
    # FULL variant leaves everything intact
    full = emit_variant(kept, "sid2", {"r1"}, stub=False)
    assert {r["uuid"]: r for r in full}["u1"]["message"]["content"][0]["content"] == "old body of a.py"


def test_apply_edits_replays_onto_worktree(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("value = 1\n")
    apply_edits(str(tmp_path), [(1, "Edit", {"file_path": str(f), "old_string": "value = 1", "new_string": "value = 2"})])
    assert f.read_text() == "value = 2\n"
    apply_edits(str(tmp_path), [(2, "Write", {"file_path": str(f), "content": "brand new\n"})])
    assert f.read_text() == "brand new\n"


def test_analyze_continuation_counts_rereads_and_edits():
    src_uuids = {"u0", "a1", "u1"}
    cont = [_asst("c1", "Read", "r9", {"file_path": "a.py"}),      # re-reads retired a.py
            _asst("c2", "Edit", "e9", {"file_path": "a.py", "old_string": "p", "new_string": "q"})]
    out = analyze_continuation(cont, src_uuids, {"a.py"})
    assert out["reread_retired"] == 1 and out["reread_files"] == ["a.py"]
    assert out["edited"] == ["a.py"] and out["cont_records"] == 2
