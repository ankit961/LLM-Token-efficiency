"""B1.2 — graph-informed ranking tests (Transparent Reduction Contract v0.1 §3.1–3.2).

Deterministic, zero model cost. A tiny fixed graph makes proximity, ranking, the simple-vs-
graph comparison, and the live hook path all reproducible:

    src/core.py::core.run      <- TOUCHED anchor
    src/near.py::near.helper   --CALLS--> core.run     (distance 1, high relevance)
    src/far.py::far.thing      (no edges, irrelevant)
    src/other.py::other.helper (shares the short name 'helper' -> ambiguous MENTION)
"""
import io
import json
import sqlite3

from contextruntime import SCHEMA_VERSION, doctor
from contextruntime.model import CodeSymbol
from contextruntime.reducers import graphrank, hook as hook_mod, livecas
from contextruntime.reducers.graphrank import WorkingSet, build_working_set, path_scores, compare_search
from contextruntime.reducers.library import reduce_search
from contextruntime.store import GraphStore

REPO = "repo"


def _sym(path, qn):
    return CodeSymbol(symbol_id=f"{REPO}::{path}::{qn}", repo_id=REPO, language="python",
                      kind="function", qualified_name=qn, path=path, start_line=1, end_line=5,
                      signature=None, content_hash=None, parser="python_ast",
                      resolution_quality=0.95, schema_version=SCHEMA_VERSION)


def _build_graph(path=":memory:") -> GraphStore:
    s = GraphStore(path)
    for p, qn in [("src/core.py", "core.run"), ("src/near.py", "near.helper"),
                  ("src/far.py", "far.thing"), ("src/other.py", "other.helper")]:
        s.put_symbol(_sym(p, qn))
    # near.helper CALLS core.run — so core (anchor) is 1 hop from near
    s.add_code_edge(REPO, f"{REPO}::src/near.py::near.helper",
                    f"{REPO}::src/core.py::core.run", "CALLS", 0.9, "exact", match_kind="exact")
    s.commit()
    return s


def _touched_core():
    return WorkingSet(frozenset({"src/core.py"}), frozenset())


# --------------------------------------------------------------------- proximity / path_scores
def test_path_scores_rank_by_graph_distance():
    s = _build_graph()
    sc = path_scores(s, REPO, ["src/core.py", "src/near.py", "src/far.py"], _touched_core())
    assert sc["src/core.py"] > sc["src/near.py"] > 0     # anchor > 1-hop neighbor > 0
    assert "src/far.py" not in sc                          # unreachable → no score
    s.close()


def test_path_scores_empty_without_anchors():
    s = _build_graph()
    # a working set whose touched path matches no indexed file → no anchors → no scores
    ws = WorkingSet(frozenset({"nonexistent/x.py"}), frozenset())
    assert path_scores(s, REPO, ["src/core.py"], ws) == {}
    assert path_scores(s, REPO, ["src/core.py"], WorkingSet(frozenset(), frozenset())) == {}
    s.close()


# --------------------------------------------------------------------- MENTIONS (privacy-safe)
def test_build_working_set_resolves_unique_mention_only():
    s = _build_graph()
    ws = build_working_set(s, REPO, prompt_text="please fix the run function")
    assert f"{REPO}::src/core.py::core.run" in ws.mentioned_symbols   # unique short name 'run'
    s.close()


def test_build_working_set_drops_ambiguous_mention():
    s = _build_graph()
    ws = build_working_set(s, REPO, prompt_text="update the helper please")   # 'helper' x2 → ambiguous
    assert not any("helper" in m for m in ws.mentioned_symbols)       # never mass-anchored
    s.close()


def test_mentions_only_store_resolved_symbol_ids_not_raw_tokens():
    s = _build_graph()
    ws = build_working_set(s, REPO, prompt_text="run zzz_nonexistent_token")
    # only the resolved symbol_id is kept; the unresolved raw token never appears
    assert all(m.startswith(f"{REPO}::") for m in ws.mentioned_symbols)
    assert not any("zzz_nonexistent_token" in m for m in ws.mentioned_symbols)
    s.close()


# --------------------------------------------------------------------- reduce_search graph mode
# far.py matches come FIRST (fill the budget in file order); the relevant core/near matches
# come LAST — so simple order drops them and graph ranking must promote them.
_RAW = "\n".join([f"src/far.py:{i}: irrelevant_{i}" for i in range(80)]
                 + ["src/core.py:1: RELEVANT_CORE", "src/near.py:2: RELEVANT_NEAR"])


def test_graph_ranking_promotes_relevant_matches_simple_drops():
    s = _build_graph()
    sc = path_scores(s, REPO, ["src/core.py", "src/near.py", "src/far.py"], _touched_core())
    simple = reduce_search(_RAW, {}, budget_tokens=64)
    graph = reduce_search(_RAW, {}, budget_tokens=64, path_scores=sc)
    assert "RELEVANT_CORE" not in simple.reduced_text      # simple keeps early far matches
    assert "RELEVANT_CORE" in graph.reduced_text           # graph keeps the relevant one
    assert graph.handle == simple.handle                    # same payload, same recovery handle
    s.close()


def test_path_scores_none_is_byte_identical_to_b1_1():
    a = reduce_search(_RAW, {}, budget_tokens=64)
    b = reduce_search(_RAW, {}, budget_tokens=64, path_scores=None)
    c = reduce_search(_RAW, {}, budget_tokens=64, path_scores={})    # empty == no ranking
    assert a.reduced_text == b.reduced_text == c.reduced_text


# --------------------------------------------------------------------- compare (the B1.2 deliverable)
def test_compare_search_reports_promotions():
    s = _build_graph()
    cmp = compare_search(_RAW, s, REPO, _touched_core(), budget_tokens=64)
    assert cmp["graph_active"] and cmp["scored_paths"] >= 2
    assert any("RELEVANT_CORE" in ln for ln in cmp["promoted"])
    # both arms preserve the recovery handle
    assert cmp["simple"].handle == cmp["graph"].handle
    s.close()


# --------------------------------------------------------------------- live hook path (integration)
def _build_journal(path, session_id, touched_path):
    """A minimal HookJournal-shaped sqlite: touched_from_journal only needs these columns."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tool_events (session_id TEXT, kind TEXT, path_normalized TEXT)")
    conn.execute("INSERT INTO tool_events VALUES (?,?,?)", (session_id, "read", touched_path))
    conn.commit()
    conn.close()


def test_hook_uses_graph_ranking_end_to_end(tmp_path, monkeypatch, capsys):
    graph_db = str(tmp_path / "codegraph.db")
    _build_graph(graph_db).close()
    journal = str(tmp_path / "journal.db")
    _build_journal(journal, "sess-1", "src/core.py")
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v,
                   "CR_DB": str(tmp_path / "live.db"), "CR_DECISION_LOG": str(tmp_path / "d.jsonl"),
                   "CR_GRAPH_DB": graph_db, "CR_REPO_ID": REPO, "CR_JOURNAL_DB": journal,
                   "CR_REDUCE_BUDGET": "64"}.items():
        monkeypatch.setenv(k, val)
    event = {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "tool_response": _RAW,
             "session_id": "sess-1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert hook_mod.main() == 0
    stdout = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["updatedToolOutput"]
    assert "RELEVANT_CORE" in stdout                        # the session's touched file was promoted
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["graph_ranked"] is True and rec["graph_scored_paths"] >= 2


def test_hook_fails_open_to_simple_when_no_graph(tmp_path, monkeypatch, capsys):
    """No CR_GRAPH_DB → ranking inert, behavior identical to B1.1 (graph_ranked=false)."""
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v,
                   "CR_DB": str(tmp_path / "live.db"), "CR_DECISION_LOG": str(tmp_path / "d.jsonl"),
                   "CR_REDUCE_BUDGET": "64"}.items():
        monkeypatch.setenv(k, val)
    monkeypatch.delenv("CR_GRAPH_DB", raising=False)
    event = {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "tool_response": _RAW,
             "session_id": "sess-1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert hook_mod.main() == 0
    stdout = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["updatedToolOutput"]
    assert "RELEVANT_CORE" not in stdout                    # simple order — relevant match dropped
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["graph_ranked"] is False


# --------------------------------------------------------------------- graph-quality repairs
def test_path_matching_respects_component_boundaries():
    from contextruntime.reducers.graphrank import _path_matches, _suffix_match
    assert not _path_matches("src/notfoo.py", frozenset({"foo.py"}))   # the old bare-endswith bug
    assert not _suffix_match("bar.py", "foobar.py")
    assert _path_matches("/repo/src/a.py", frozenset({"src/a.py"}))     # abs vs repo-relative still OK
    assert _path_matches("src/a.py", frozenset({"src/a.py"}))


def test_proximity_max_depth_is_exact():
    """Chain a→b→c→d→e (each CALLS the next), anchor a, MAX_DEPTH=3: d (3 hops) is scored, e
    (4 hops) is NOT — the best-first relaxation caps expansion at exactly MAX_DEPTH."""
    s = _build_graph()   # reuse the 4-symbol graph, then extend into a chain
    names = ["a", "b", "c", "d", "e"]
    for nm in names:
        s.put_symbol(_sym(f"src/{nm}.py", f"{nm}.fn"))
    for src, dst in zip(names, names[1:]):
        s.add_code_edge(REPO, f"{REPO}::src/{src}.py::{src}.fn",
                        f"{REPO}::src/{dst}.py::{dst}.fn", "CALLS", 0.9, "exact", match_kind="exact")
    s.commit()
    ws = WorkingSet(frozenset({"src/a.py"}), frozenset())
    sc = path_scores(s, REPO, [f"src/{nm}.py" for nm in names], ws)
    assert "src/d.py" in sc and "src/e.py" not in sc          # 3 hops in, 4 hops out
    assert sc["src/a.py"] > sc["src/b.py"] > sc["src/c.py"] > sc["src/d.py"]   # monotone decay
    s.close()
