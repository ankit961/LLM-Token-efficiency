"""ContextPolicy probe + per-session advisory dashboard (Slice 2 measurement).

Verifies opportunity detection (indexed source read with a smaller signature-index), C10 exclusion
(a read of a file the session edits is never an opportunity), the semantic-telemetry join
(reads used / materialized / expansions / retries), and fallback accounting.
"""
import os
import sqlite3

from contextruntime.policydash import build_dashboard, format_dashboard
from contextruntime.policyprobe import probe_source_read
from contextruntime.store import GraphStore

REPO = "demo"


def _graph(tmp_path):
    g = GraphStore(str(tmp_path / "graph.db"))
    def sym(path, qn, kind, sig):
        g.conn.execute(
            "INSERT INTO symbols(symbol_id,repo_id,language,kind,qualified_name,path,signature,"
            "start_line,end_line,parser,resolution_quality,schema_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{REPO}::{path}::{qn}", REPO, "python", kind, qn, path, sig, 1, 9,
             "python_ast", 0.9, "0.10.0"))
    # big.py: 3 short-signature defs -> tiny skeleton, so a full read is an opportunity
    sym("big.py", "big", "module", "")
    sym("big.py", "big.a", "function", "def a(x):")
    sym("big.py", "big.b", "function", "def b(y):")
    sym("big.py", "big.c", "function", "def c(z):")
    # editme.py: also indexed, but the session edits it -> C10 excludes it
    sym("editme.py", "editme.f", "function", "def f():")
    g.conn.commit()
    return g


def _journal(tmp_path, rows):
    p = str(tmp_path / "journal.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tool_events(seq INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, "
              "representation TEXT, success INTEGER, tool_name TEXT, path_normalized TEXT, "
              "repo_relative TEXT, model_visible_tokens INTEGER)")
    for i, r in enumerate(rows):
        c.execute("INSERT INTO tool_events(seq,session_id,kind,representation,success,tool_name,"
                  "path_normalized,repo_relative,model_visible_tokens) VALUES(?,?,?,?,?,?,?,?,?)",
                  (i + 1, "s1", r["kind"], r.get("representation", "file"), 1, r.get("tool", "Read"),
                   None, r["rel"], r.get("tok", 0)))
    c.commit(); c.close()
    return p


def _add_semantic_read(graph_db, path, transport, expansion=0, recovery=0):
    c = sqlite3.connect(graph_db)
    n = c.execute("SELECT count(*) FROM semantic_reads").fetchone()[0]
    c.execute("INSERT INTO semantic_reads(event_id,channel,allowed,denied,nudged,schema_version,"
              "session_id,path,transport_content_tokens,expansion_tokens,recovery_turns) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (f"ev-{n}", "mcp", 1, 0, 0, "0.10.0", "s1", path, transport, expansion, recovery))
    c.commit(); c.close()


# --------------------------------------------------------------------------- probe
def test_probe_flags_indexed_big_file_as_opportunity(tmp_path):
    g = _graph(tmp_path)
    p = probe_source_read(g, REPO, "big.py", raw_tokens=800)
    assert p.indexed and p.n_defs == 3 and p.is_opportunity
    assert 0 < p.skeleton_tokens < 800 and p.avoidable_tokens == 800 - p.skeleton_tokens
    g.close()


def test_probe_small_read_is_not_an_opportunity(tmp_path):
    g = _graph(tmp_path)
    p = probe_source_read(g, REPO, "big.py", raw_tokens=3)   # raw smaller than the skeleton
    assert p.indexed and not p.is_opportunity and p.avoidable_tokens == 0
    g.close()


def test_probe_unindexed_file_is_not_source(tmp_path):
    g = _graph(tmp_path)
    p = probe_source_read(g, REPO, "unknown.py", raw_tokens=999)
    assert not p.indexed and not p.is_opportunity
    g.close()


# --------------------------------------------------------------------------- dashboard
def test_dashboard_counts_opportunities_and_c10_and_fallbacks(tmp_path):
    g = _graph(tmp_path); g.close()
    journal = _journal(tmp_path, [
        {"kind": "read", "rel": "big.py", "tok": 800},        # opportunity
        {"kind": "read", "rel": "editme.py", "tok": 500},     # indexed but edited -> C10, not opp
        {"kind": "read", "rel": "README.md", "tok": 300},     # repo file, not indexed -> not opp
        {"kind": "edit", "rel": "editme.py"},                 # the edit that triggers C10
    ])
    d = build_dashboard(journal, str(tmp_path / "graph.db"), REPO)
    assert d.native_source_reads == 3                          # 3 file reads
    assert d.semantic_opportunities == 1                       # only big.py
    assert d.raw_equivalent_tokens == 1600
    assert d.estimated_avoided_tokens > 0
    assert d.semantic_reads_used == 0 and d.fallbacks == 1     # opportunity not taken


def test_dashboard_join_with_semantic_telemetry(tmp_path):
    g = _graph(tmp_path); g.close()
    graph_db = str(tmp_path / "graph.db")
    journal = _journal(tmp_path, [{"kind": "read", "rel": "big.py", "tok": 800}])
    _add_semantic_read(graph_db, "big.py", transport=120, expansion=15, recovery=1)
    d = build_dashboard(journal, graph_db, REPO)
    assert d.semantic_opportunities == 1
    assert d.semantic_reads_used == 1 and d.semantic_materialized_tokens == 120
    assert d.expansions == 1 and d.retries == 1
    assert d.fallbacks == 0                                    # the opportunity WAS taken


def test_dashboard_derives_repo_relative_from_absolute_path(tmp_path):
    g = _graph(tmp_path); g.close()
    root = "/work/demo"
    p = str(tmp_path / "j.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tool_events(seq INTEGER PRIMARY KEY, session_id TEXT, kind TEXT, "
              "representation TEXT, success INTEGER, tool_name TEXT, path_normalized TEXT, "
              "repo_relative TEXT, model_visible_tokens INTEGER)")
    c.execute("INSERT INTO tool_events VALUES(1,'s1','read','file',1,'Read',?,NULL,800)",
              (root + "/big.py",))
    c.commit(); c.close()
    d = build_dashboard(p, str(tmp_path / "graph.db"), REPO, repo_root=root)
    assert d.native_source_reads == 1 and d.semantic_opportunities == 1


def test_format_matches_dashboard_shape(tmp_path):
    g = _graph(tmp_path); g.close()
    journal = _journal(tmp_path, [{"kind": "read", "rel": "big.py", "tok": 42800}])
    out = format_dashboard(build_dashboard(journal, str(tmp_path / "graph.db"), REPO))
    assert "ContextRuntime — advisory" in out
    assert "native source reads" in out and "estimated avoided" in out and "k" in out
