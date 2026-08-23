"""merged_records: one API call (split across assistant records sharing a requestId) → one record."""
import json

from corpus.transcript_util import merged_records, real_turns

_U = {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 10, "input_tokens": 1, "output_tokens": 5}


def _write(tmp_path, rows):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


def test_split_assistant_records_merge_into_one_call(tmp_path):
    rows = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "requestId": "r1", "message": {"usage": _U, "content": [{"type": "thinking", "thinking": ""}]}},
        {"type": "assistant", "requestId": "r1", "message": {"usage": _U, "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
        {"type": "assistant", "requestId": "r2", "message": {"usage": _U, "content": [{"type": "text", "text": "done"}]}},
    ]
    recs = list(merged_records(_write(tmp_path, rows)))
    assistants = [r for r in recs if r["type"] == "assistant"]
    assert len(assistants) == 2                                          # r1 merged, r2 separate
    assert [b["type"] for b in assistants[0]["message"]["content"]] == ["thinking", "tool_use"]
    assert real_turns(_write(tmp_path, rows)) == 2                        # 3 records, 2 real API calls
    # usage is kept ONCE per call (no double counting)
    total = sum(r["message"]["usage"]["cache_read_input_tokens"] for r in assistants)
    assert total == 200


def test_records_without_requestid_pass_through(tmp_path):
    rows = [{"type": "queue-operation", "x": 1}, {"type": "user", "message": {"content": "a"}}]
    assert [r["type"] for r in merged_records(_write(tmp_path, rows))] == ["queue-operation", "user"]
