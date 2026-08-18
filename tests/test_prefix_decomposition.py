"""B2.0 seed tests — representation bucketing (tiny journal) + tool-category mapping. Zero quota."""
import json
import sqlite3

from corpus.prefix_decomposition import (accumulated_composition, category,
                                         read_bucket_by_representation, session_reduction_ceiling)


def test_category_maps_tools_to_prefix_buckets():
    assert category("Read") == "file_read" and category("Grep") == "search"
    assert category("Bash") == "bash" and category("Edit") == "edit_echo"
    assert category("mcp__contextruntime__context_expand") == "mcp"
    assert category("SomethingNew") == "other_tool" and category(None) == "other_tool"


def test_read_bucket_by_representation_sums_and_totals(tmp_path):
    db = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE tool_events (kind TEXT, representation TEXT, model_visible_tokens INT)")
    conn.executemany("INSERT INTO tool_events VALUES (?,?,?)", [
        ("read", "file", 1000), ("read", "file", 500),      # file 1500
        ("read", "search", 300),                             # search 300
        ("edit", "file", 9999),                              # not a read ⇒ ignored
    ])
    conn.commit(); conn.close()
    out = read_bucket_by_representation(str(tmp_path / "*.sqlite"))
    assert out["file"]["tokens"] == 1500 and out["file"]["events"] == 2
    assert out["search"]["tokens"] == 300
    assert out["TOTAL"]["tokens"] == 1800 and out["TOTAL"]["events"] == 3


def test_accumulated_composition_buckets_tool_outputs(tmp_path):
    lines = [
        {"type": "assistant", "message": {"usage": {"cache_creation_input_tokens": 13000},
            "content": [{"type": "tool_use", "id": "u1", "name": "Read", "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "u1", "content": "def f():\n    return 1\n" * 20}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u2", "name": "Bash", "input": {}},
            {"type": "text", "text": "thinking about it"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "u2", "content": "test output here"}]}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    comp = accumulated_composition(str(p))
    assert comp["tool_outputs_by_category"]["file_read"] > comp["tool_outputs_by_category"]["bash"]
    assert comp["system_tools_floor_tokens"] == 13000        # first cache-creation = fixed floor
    assert comp["assistant_text_tokens"] > 0


def test_reduction_ceiling_spares_edited_files_and_compounds(tmp_path):
    body = "x = 1\n" * 400                                     # a big-ish read
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 1},                 # turn 1
            "content": [{"type": "tool_use", "id": "rA", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "rA", "content": body}]}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 1},                 # turn 2
            "content": [{"type": "tool_use", "id": "rB", "name": "Read", "input": {"file_path": "b.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "rB", "content": body}]}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 1},                 # turn 3 — edits b.py
            "content": [{"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "b.py"}}]}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [{"type": "text", "text": "."}]}},  # turn 4
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [{"type": "text", "text": "."}]}},  # turn 5
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    r = session_reduction_ceiling(str(p), t_total=1_000_000, reduce_frac=0.8)
    assert r["total_turns"] == 5 and r["file_read_events"] == 2
    assert r["reducible_events"] == 1 and r["spared_events"] == 1   # a.py reducible, b.py edited→spared
    # a.py read at turn 1 ⇒ present for 4 later turns; raw counts b.py too ⇒ raw saving > reducible
    assert r["compounded_saving_raw"] > r["compounded_saving_reducible"] > 0
