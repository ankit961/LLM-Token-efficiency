"""B2 bash/test-output go/no-go — detector + parser + compounding ceiling. Zero quota."""
import json

from contextruntime.reducers.base import tokens
from corpus.bash_output_replay import (bash_reduction_ceiling, is_test_like, parse_bash_outputs)

_PYTEST = ("============ test session starts ============\n"
           + "\n".join(f"tests/test_{i}.py::test_case PASSED" for i in range(120))
           + "\n1 failed, 119 passed in 3.2s\n"
           "FAILED tests/test_9.py::test_case - AssertionError: nope\n")


def test_is_test_like():
    assert is_test_like(_PYTEST)
    assert is_test_like("Ran 42 tests in 1.2s\nOK")                       # django/unittest
    assert not is_test_like("diff --git a/x.py b/x.py\n+import os\n")     # a git diff is not test output
    assert not is_test_like("total 12\ndrwxr-xr-x  3 user  staff")        # ls output


def test_parse_bash_outputs(tmp_path):
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "pytest"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b1", "content": _PYTEST}]}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
            {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "content": "x = 1"}]}},   # a Read, not Bash
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    outs, total_turns = parse_bash_outputs(str(p))
    assert len(outs) == 1 and is_test_like(outs[0][1])                    # only the Bash result
    assert total_turns == 2                                               # 2 assistant turns in the session


def test_ceiling_summarizes_passing_tail(tmp_path):
    # pytest output at turn 1 of a 10-turn session ⇒ compounding over 9 later turns
    lines = [{"type": "assistant", "message": {"usage": {"input_tokens": 1}, "content": [
                 {"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]}},
             {"type": "user", "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "b1", "content": _PYTEST}]}}]
    lines += [{"type": "assistant", "message": {"usage": {"input_tokens": 1},
               "content": [{"type": "text", "text": "."}]}} for _ in range(9)]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    r = bash_reduction_ceiling(str(p), t_total=1_000_000, floor=200)
    assert r["fired"] == 1 and r["compounded_saving"] > 0 and r["pct_of_T_total"] > 0
    assert r["testlike_tokens"] == tokens(_PYTEST)
