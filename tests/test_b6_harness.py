"""B6 harness — grading label conversion (all three SWE-bench name formats), patch application,
and the live-runner's pure metric functions. The live claude/proxy path is not exercised in CI."""
import json
import subprocess

from corpus.b6_grading import apply_patch, resolvable, to_label
from corpus.b6_live_ab import DISALLOW_ADMISSION, gc_rereads, transcript_metrics


def test_to_label_three_formats():
    assert to_label("test_x (m.C)") == "m.C.test_x"                       # old
    assert to_label("test_x (m.C.test_x)") == "m.C.test_x"                # new (parenthetical complete)
    assert to_label("Some docstring sentence. (m.C.test_y)") == "m.C.test_y"   # docstring + id
    assert to_label("already.dotted.Label.test_z") == "already.dotted.Label.test_z"


def test_resolvable_skips_bare_docstrings():
    labels, skipped = resolvable(["test_a (m.C.test_a)", "bare docstring with no id",
                                  "another sentence.", "test_b (m.C)"])
    assert labels == ["m.C.test_a", "m.C.test_b"] and skipped == 2


def test_apply_patch_on_tmp_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 one
-two
+TWO
"""
    assert apply_patch(str(tmp_path), patch)
    assert f.read_text() == "one\nTWO\n"
    assert not apply_patch(str(tmp_path), "garbage not a patch")


def test_disallow_list_keeps_the_working_tools():
    for essential in ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "TodoWrite", "ScheduleWakeup"):
        assert essential not in DISALLOW_ADMISSION
    assert "Workflow" in DISALLOW_ADMISSION and "mcp__claude_ai_Gmail__*" in DISALLOW_ADMISSION


def test_transcript_metrics_counts_real_calls(tmp_path):
    u = {"cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50, "input_tokens": 2, "output_tokens": 7}
    rows = [
        {"type": "assistant", "requestId": "r1", "message": {"usage": u, "content": [
            {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "x/a.py"}}]}},
        {"type": "assistant", "requestId": "r1", "message": {"usage": u, "content": [
            {"type": "text", "text": "same call, split record"}]}},
        {"type": "assistant", "requestId": "r2", "isSidechain": True, "message": {"usage": u, "content": []}},
        {"type": "assistant", "requestId": "r3", "message": {"usage": {**u, "cache_read_input_tokens": 2000},
                                                             "content": [{"type": "text", "text": "done"}]}},
    ]
    tp = tmp_path / "s.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    m = transcript_metrics(str(tp))
    assert m["calls"] == 2                                    # merged r1 + r3; sidechain excluded
    assert m["sum_input"] == (1052) + (2052)
    assert m["peak_P"] == 2052
    assert m["reads"] == [(1, "x/a.py")]


def test_gc_rereads_parses_gateway_log(tmp_path):
    log = tmp_path / "gw.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"turn": 10, "mode": "enforce", "applied": 3, "thinking_stripped": 2},
        {"turn": 11, "mode": "enforce", "applied": 0, "thinking_stripped": 1},
        {"fallback_original": True, "upstream_status": 400},
        {"response_usage": {"input_tokens": 2}},
    ]))
    g = gc_rereads(str(log), [])
    assert g == {"tool_results_retired": 3, "thinking_blocks_stripped": 3, "fallback_original": 1}
    assert gc_rereads(str(tmp_path / "missing.jsonl"), []) is None
