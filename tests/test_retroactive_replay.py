"""B2.v2 — retroactive (compact-after-last-touch) file-residency ceiling. Zero quota."""
import json

from contextruntime.reducers.base import tokens
from corpus.retroactive_replay import _session_turns, retroactive_file_ceiling

_FILE = "\n".join(f"line_{i} = {i}" for i in range(300))          # a big file


def _transcript(tmp_path, read_turn_pad):
    """Read a.py at turn 1, then `read_turn_pad` more assistant turns with no further touch of a.py."""
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "content": _FILE}]}},
    ]
    lines += [{"type": "assistant", "message": {"usage": {"input_tokens": 1},
               "content": [{"type": "text", "text": "."}]}} for _ in range(read_turn_pad)]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return str(p)


def test_session_turns_counts_assistant_turns(tmp_path):
    assert _session_turns(_transcript(tmp_path, 9)) == 10               # 1 read-turn + 9 pad


def test_retroactive_saving_grows_with_abandoned_tail(tmp_path):
    short = retroactive_file_ceiling(_transcript(tmp_path, 2), t_total=1_000_000)   # tail = 2 turns
    long = retroactive_file_ceiling(_transcript(tmp_path / "d", 20) if False
                                    else _transcript(tmp_path, 20), t_total=1_000_000)  # tail = 20
    assert short["files"] == 1 and short["gross_saving"] > 0
    assert long["gross_saving"] > short["gross_saving"]                 # longer abandoned tail ⇒ more
    # gross = 0.8 * footprint * (total_turns - last_touch); last_touch=1, so it scales with turns
    assert long["pct_of_T_total"] > short["pct_of_T_total"]


def test_no_saving_when_file_touched_till_the_end(tmp_path):
    # a file whose only read is the LAST turn ⇒ no abandoned tail ⇒ ~0 retroactive saving
    lines = [{"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
                 {"type": "text", "text": "."}]}} for _ in range(5)]
    lines += [
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "content": _FILE}]}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    r = retroactive_file_ceiling(str(p), t_total=1_000_000)
    assert r["gross_saving"] == 0                                       # read at the last turn ⇒ no tail
