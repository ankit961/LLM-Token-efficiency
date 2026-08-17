"""Step-6 paired-replay tests — parse a synthetic transcript, reduce paired, score recall. Zero quota."""
import json

from corpus.paired_replay import (_needed_paths, _retained_paths, collect, grid, paired_reduction,
                                  parse_transcript, pooled_recall, recall_analysis)


def _transcript(tmp_path):
    """A tiny transcript: a big Bash grep (reducible), then the agent Reads one matched file."""
    big = "\n".join(f"src/mod{i}.py:{i}: def handler_{i}(): return helper_{i}()" for i in range(120))
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "grep -rn handler src/"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "u1", "content": big}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u2", "name": "Read", "input": {"file_path": "src/mod0.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "u2", "content": "def handler_0(): ..."}]}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return str(p)


def test_parse_extracts_search_and_touch(tmp_path):
    evs = parse_transcript(_transcript(tmp_path))
    kinds = [(e.kind, e.tool) for e in evs]
    assert ("search", "Bash") in kinds and ("touch", "Read") in kinds
    se = [e for e in evs if e.kind == "search"]
    assert se and "handler" in se[0].raw_output and se[0].tool_input["command"].startswith("grep")


def test_paired_reduction_reduces_the_big_grep(tmp_path):
    se = [e for e in parse_transcript(_transcript(tmp_path)) if e.kind == "search"]
    r = paired_reduction(se, budget=256, floor=400)
    assert r["reductions_fired"] == 1 and 0.0 < r["R_paired"] < 1.0     # a big grep does reduce
    assert r["reduced_tokens"] < r["raw_tokens"]


def test_grid_shape_and_floor_is_the_lever(tmp_path):
    se = [e for e in parse_transcript(_transcript(tmp_path)) if e.kind == "search"]
    g = grid(se, budgets=(64,), floors=(125, 400))
    assert len(g) == 2
    lo = next(x for x in g if x["floor"] == 125)["R_paired"]
    hi = next(x for x in g if x["floor"] == 400)["R_paired"]
    assert lo >= hi                                                    # a lower floor never reduces less


def test_recall_keeps_the_subsequently_read_file(tmp_path):
    evs = parse_transcript(_transcript(tmp_path))
    se = [e for e in evs if e.kind == "search"]
    a = recall_analysis(evs, se, budget=256, floor=400, k=8)
    # mod0.py matched AND was read next → it must be named in the compact output (kept line/rollup)
    assert a["needed_paths"] >= 1 and a["path_recall"] == 1.0 and a["reductions_with_a_miss"] == 0


def test_matching_is_collision_safe_not_basename():
    # a grep hit in django/db/models/query.py; the agent then touches a DIFFERENT models.py.
    matched = ["django/db/models/query.py", "django/contrib/admin/models.py"]
    # absolute worktree touch-path for the query.py file only:
    future = {"/tmp/wt/django/db/models/query.py"}
    needed = _needed_paths(matched, future)
    assert needed == {"django/db/models/query.py"}                 # suffix-matches across abs/rel
    assert "django/contrib/admin/models.py" not in needed          # NOT conflated by basename
    # retained check is full-path substring, so a different models.py in the text can't satisfy it
    text = "matches by file: django/contrib/admin/models.py×3"
    assert _retained_paths(text, needed) == set()                  # query.py genuinely absent → miss


def test_pooled_recall_sums_across_transcripts(tmp_path):
    # two transcripts in separate dirs → collect() finds both; pooled needs = 2× a single file's
    d1, d2 = tmp_path / "s1", tmp_path / "s2"
    d1.mkdir(); d2.mkdir()
    _transcript(d1); _transcript(d2)
    per = collect(str(tmp_path / "*" / "*.jsonl"))
    assert len(per) == 2
    single = recall_analysis(*(lambda e: (e, [x for x in e if x.kind == "search"]))(
        parse_transcript(str(d1 / "t.jsonl"))), budget=256, floor=400)
    r = pooled_recall(per, budget=256, floor=400)
    assert r["needed_paths"] == 2 * single["needed_paths"] >= 2    # summed across the two files
    assert r["path_recall"] == 1.0 and r["reductions_with_a_miss"] == 0
