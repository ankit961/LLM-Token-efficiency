"""B5.1 call-collapse oracle — classification, derivability, runs, packets, retention semantics."""
import json

from corpus.call_collapse_oracle import (build_packet, choice_breadth, classify_call, retention,
                                         segment_runs, targets_of, transition_class)


def _call(idx, cls=None, tools=(), out="", P=50000, out_tok=50):
    c = {"idx": idx, "tools": list(tools), "out_text": out, "P": P, "out_tokens": out_tok}
    c["cls"] = cls or classify_call(c["tools"])
    return c


def test_classify_call_conservative():
    assert classify_call([("Read", {"file_path": "a.py"})]) == "discovery"
    assert classify_call([("Bash", {"command": "grep -rn foo src/"})]) == "discovery"
    assert classify_call([("Bash", {"command": "git log --oneline -5"})]) == "discovery"
    assert classify_call([("Bash", {"command": "cat > /tmp/repro.py << 'EOF'\nx\nEOF"})]) == "other_bash"   # redirect = write
    assert classify_call([("Bash", {"command": "cat a.py | tee b.py"})]) == "other_bash"
    assert classify_call([("Bash", {"command": "python -m pytest tests/"})]) == "test_exec"
    assert classify_call([("Edit", {"file_path": "a.py"})]) == "edit"
    assert classify_call([("Read", {"file_path": "a.py"}), ("Edit", {"file_path": "a.py"})]) == "edit"   # state change dominates
    assert classify_call([]) == "no_tool"


def test_transition_D0_D1_D2():
    # D0: next reads a path that appeared in the PREVIOUS output
    nxt = _call(2, tools=[("Read", {"file_path": "django/db/models/query.py"})])
    assert transition_class(nxt, "hits:\ndjango/db/models/query.py:100: def filter(", "", set()) == "D0"
    # D1: path appeared earlier in the run (accumulated history), not in the immediately previous output
    hist = "earlier output mentioned django/db/models/sql/compiler.py here"
    nxt2 = _call(3, tools=[("Read", {"file_path": "django/db/models/sql/compiler.py"})])
    assert transition_class(nxt2, "unrelated previous output", hist, set()) == "D1"
    # D1: parameter-free readonly inspection
    nxt3 = _call(3, tools=[("Bash", {"command": "git status"})])
    assert transition_class(nxt3, "anything", "anything", set()) == "D1"
    # D2: grep pattern whose identifier appears nowhere in run history
    nxt4 = _call(3, tools=[("Grep", {"pattern": "RelabeledCol"})])
    assert transition_class(nxt4, "no such name here", "nor here", set()) == "D2"


def test_segment_runs_and_state_change_breaks():
    calls = [_call(1, tools=[("Read", {"file_path": "a.py"})]),
             _call(2, tools=[("Bash", {"command": "grep foo a.py"})]),
             _call(3, tools=[("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"})]),
             _call(4, tools=[("Read", {"file_path": "b.py"})])]
    runs = segment_runs(calls)
    assert len(runs) == 2
    assert [c["idx"] for c in runs[0]["calls"]] == [1, 2] and runs[0]["next"]["idx"] == 3
    assert [c["idx"] for c in runs[1]["calls"]] == [4] and runs[1]["next"] is None


def test_packet_dedup_keeps_latest_read():
    calls = [_call(1, tools=[("Read", {"file_path": "a.py"})], out="OLD BODY OF A"),
             _call(2, tools=[("Bash", {"command": "grep x"})], out="grep says x"),
             _call(3, tools=[("Read", {"file_path": "a.py"})], out="NEW BODY OF A"),
             _call(4, tools=[("Bash", {"command": "grep x"})], out="grep says x")]     # duplicate output
    text, ptok, raw = build_packet(calls)
    assert "NEW BODY OF A" in text and "OLD BODY OF A" not in text                      # latest read wins
    assert text.count("grep says x") == 1                                               # hash dedup
    assert ptok < raw


def test_retention_lost_vs_elsewhere_semantics():
    line = "def compute_thing(self): return self.a + self.b + 42"
    run = {"calls": [_call(1, tools=[("Read", {"file_path": "pkg/mod.py"})], out=f"  1\t{line}\n")],
           "next": _call(2, tools=[("Edit", {"file_path": "pkg/mod.py", "old_string": line, "new_string": "x"})])}
    text, _, _ = build_packet(run["calls"])
    r = retention(run, text)
    assert r["next_action_ok"] and r["evidence_from_this_run"]                          # in raw AND in packet
    # evidence NOT in the run at all -> neutral (prior context survives collapse), still ok
    run2 = {"calls": [_call(1, tools=[("Read", {"file_path": "other.py"})], out="unrelated content here entirely")],
            "next": run["next"]}
    r2 = retention(run2, build_packet(run2["calls"])[0])
    assert r2["next_action_ok"] and not r2["evidence_from_this_run"]
    # evidence in raw but MISSING from packet -> retention failure
    r3 = retention(run, "packet that lost the source line")
    assert not r3["next_action_ok"] and not r3["edit_region_in_packet"]


def test_choice_breadth_rank():
    nxt = _call(2, tools=[("Read", {"file_path": "src/b.py"})])
    prev_out = "src/a.py:1: hit\nsrc/b.py:9: hit\nsrc/c.py:3: hit"
    cb = choice_breadth(nxt, prev_out)
    assert cb == {"candidates": 3, "rank": 2}
    assert choice_breadth(_call(2, tools=[("Bash", {"command": "git status"})]), prev_out) is None


def test_targets_generic_and_patterns():
    p, pat, gen = targets_of(_call(1, tools=[("Bash", {"command": "git status"})]))
    assert gen and not p and not pat
    p2, pat2, _ = targets_of(_call(1, tools=[("Grep", {"pattern": "class Col", "path": "django/db"})]))
    assert "class Col" in pat2 and "django/db" in p2
