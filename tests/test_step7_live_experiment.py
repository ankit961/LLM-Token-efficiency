"""Step-7 metric helpers — pure/db/transcript parsing, no live Claude runs."""
import json
import os
import sqlite3

from contextruntime.corpusrunner import _parse_usage_json
from corpus.step7_live_experiment import (aggregate, count_expansions, native_reread_count,
                                          t_total, transcript_for_worktree)


def test_transcript_for_worktree_targets_session_not_global_newest(tmp_path):
    # the bug this guards: picking the GLOBAL newest jsonl grabs an unrelated (orchestrator) session.
    projects = tmp_path / "projects"
    projects.mkdir()
    wt = "/x/step7/django__django-1/A_native/rep0/worktree"
    enc = "-x-step7-django--django-1-A-native-rep0-worktree"          # '/' and '_' -> '-'
    (projects / enc).mkdir()
    (projects / enc / "sess.jsonl").write_text("{}")
    other = projects / "-some-other-claude-session"                  # unrelated, and NEWER
    other.mkdir()
    (other / "big.jsonl").write_text("{}")
    got = transcript_for_worktree(wt, projects_dir=str(projects))
    assert got is not None and got.endswith(f"{enc}/sess.jsonl".replace("/", os.sep))
    assert transcript_for_worktree("/x/nope/worktree", projects_dir=str(projects)) is None


def test_parse_usage_json_extracts_usage_and_turns():
    out = json.dumps({"type": "result", "subtype": "success", "num_turns": 12,
                      "total_cost_usd": 0.34, "usage": {"input_tokens": 100, "output_tokens": 50,
                      "cache_creation_input_tokens": 20, "cache_read_input_tokens": 4000}})
    usage, turns, cost = _parse_usage_json(out)
    assert turns == 12 and cost == 0.34 and usage["cache_read_input_tokens"] == 4000
    assert t_total(usage) == 100 + 50 + 20 + 4000
    assert _parse_usage_json("not json") == (None, None, None)
    assert t_total(None) is None


def test_native_reread_count_counts_repeat_file_reads(tmp_path):
    db = str(tmp_path / "journal.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_events (kind TEXT, representation TEXT, path_normalized TEXT)")
    rows = [("read", "file", "a/models.py"), ("read", "file", "a/models.py"),   # 1 re-read
            ("read", "file", "a/models.py"),                                     # +1 re-read (3 total)
            ("read", "file", "b/views.py"),                                      # once, no re-read
            ("read", "search", "a/models.py"),                                   # search, ignored
            ("edit", "file", "a/models.py")]                                     # edit, ignored
    conn.executemany("INSERT INTO tool_events VALUES (?,?,?)", rows)
    conn.commit(); conn.close()
    assert native_reread_count(db) == 2          # models.py read 3x ⇒ 2 re-reads; views.py once ⇒ 0


def test_count_expansions_counts_context_expand_tool_uses(tmp_path):
    p = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u1", "name": "mcp__contextruntime__context_expand",
             "input": {"handle": "result://x"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u2", "name": "Read", "input": {"file_path": "a.py"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u3", "name": "mcp__contextruntime__context_expand",
             "input": {"handle": "result://y"}}]}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines))
    assert count_expansions(str(p)) == 2
    assert count_expansions(str(tmp_path / "missing.jsonl")) is None


def test_aggregate_means_by_arm_and_task():
    results = {
        "django__django-1|A_native|rep0": {"T_total": 1000, "num_turns": 10, "effective_read_tokens": 500,
            "native_rereads": 0, "result_expansions": 0, "exact_search_repeat_count": 1,
            "reductions_enforced": 0, "wall_time_s": 100},
        "django__django-1|D1_b256_f125|rep0": {"T_total": 800, "num_turns": 9, "effective_read_tokens": 300,
            "native_rereads": 1, "result_expansions": 2, "exact_search_repeat_count": 0,
            "reductions_enforced": 3, "wall_time_s": 90},
        "django__django-1|A_native|rep1": {"error": "boom"},
    }
    agg = aggregate(results)
    assert agg["arm_means"]["A_native"]["n"] == 1 and agg["arm_means"]["A_native"]["T_total"] == 1000
    assert agg["arm_means"]["D1_b256_f125"]["reductions_enforced"] == 3
    assert agg["errors"] == ["django__django-1|A_native|rep1"]
    assert "A_native" in agg["per_task"]["django__django-1"]
