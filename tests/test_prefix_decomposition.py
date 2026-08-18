"""B2.0 seed tests — representation bucketing (tiny journal) + tool-category mapping. Zero quota."""
import json
import sqlite3

from corpus.prefix_decomposition import (accumulated_composition, category,
                                         read_bucket_by_representation)


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
