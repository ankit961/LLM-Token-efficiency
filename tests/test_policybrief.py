"""ContextPolicy SessionStart advisory brief (Slice 2b steering).

The brief is the only contract-correct advisory channel (PreToolUse stdout is invisible to the
model; SessionStart stdout is injected as context). It must name the semantic tools, encode the
semantic-for-understanding / native-for-editing heuristic (C10), and fail open (print nothing) on
an unindexed repo or any error.
"""
import sqlite3

from contextruntime import policybrief as B
from contextruntime.store import GraphStore

REPO = "demo"


def _graph(tmp_path, n_defs=3):
    g = GraphStore(str(tmp_path / "graph.db"))
    g.conn.execute("INSERT INTO symbols(symbol_id,repo_id,language,kind,qualified_name,path,parser,"
                   "resolution_quality,schema_version) VALUES(?,?,?,?,?,?,?,?,?)",
                   (f"{REPO}::m.py::m", REPO, "python", "module", "m", "m.py", "python_ast", 0.9, "0.10.0"))
    for i in range(n_defs):
        g.conn.execute("INSERT INTO symbols(symbol_id,repo_id,language,kind,qualified_name,path,parser,"
                       "resolution_quality,schema_version) VALUES(?,?,?,?,?,?,?,?,?)",
                       (f"{REPO}::m.py::f{i}", REPO, "python", "function", f"f{i}", "m.py",
                        "python_ast", 0.9, "0.10.0"))
    g.conn.commit(); g.close()
    return str(tmp_path / "graph.db")


def test_brief_names_the_tools_and_the_heuristic(tmp_path):
    brief = B.build_brief(_graph(tmp_path), REPO)
    assert "read_symbol" in brief and "context_search" in brief
    assert "UNDERSTAND" in brief and "EDIT" in brief          # the semantic/native (C10) heuristic
    assert "advisory" in brief and "3 symbols" in brief


def test_brief_is_empty_on_unindexed_repo(tmp_path):
    # a fresh graph with no symbols for this repo -> nothing to steer to -> fail-open silence
    g = GraphStore(str(tmp_path / "empty.db")); g.close()
    assert B.build_brief(str(tmp_path / "empty.db"), REPO) == ""


def test_brief_is_empty_on_missing_graph(tmp_path):
    assert B.build_brief(str(tmp_path / "nope.db"), REPO) == ""


def test_run_prints_brief_on_sessionstart(tmp_path, capsys):
    graph = _graph(tmp_path)
    rc = B.run('{"hook_event_name":"SessionStart","session_id":"s1"}', graph, REPO)
    out = capsys.readouterr().out
    assert rc == 0 and "read_symbol" in out


def test_run_is_silent_on_other_events(tmp_path, capsys):
    graph = _graph(tmp_path)
    rc = B.run('{"hook_event_name":"PreToolUse"}', graph, REPO)
    assert rc == 0 and capsys.readouterr().out == ""


def test_run_fails_open_on_garbage_stdin(tmp_path, capsys):
    graph = _graph(tmp_path)
    rc = B.run("}{ not json", graph, REPO)
    # unparseable payload -> treated as no event name -> brief still emitted (fail-open, never crash)
    assert rc == 0
