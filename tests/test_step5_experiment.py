"""Step-5 A/B/C harness — deterministic tests (no agent invocation, zero quota).

Covers: arm→env wiring, search-heavy task selection from the Step-4 artifact, per-run metric
extraction from a journal + decision log, the two experiment deltas, and the hook's
non_beneficial decision-log record.
"""
import io
import json

from contextruntime import doctor
from contextruntime.hookjournal import HookJournal
from contextruntime.reducers import hook as hook_mod

from corpus.step5_experiment import ARMS, compare, run_metrics, select_search_heavy_tasks

_SENTINEL = object()


def _ev(j, *, eid, kind, step, path, tok=None, tok_attr=None, representation="file"):
    tstat = "text" if tok_attr == "attributed" else None
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": "s1",
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": eid, "tool_name": "Read", "kind": kind,
        "channel": "native_read", "mutation_source": None, "mutation_status": None,
        "representation": representation, "path_absolute": path, "path_normalized": path,
        "repo_relative": None, "repo_id": None, "pre_version": None, "post_version": None,
        "content_version": None, "version_status": "stable", "response_hash": None,
        "model_visible_chars": None, "model_visible_tokens": tok, "token_status": tstat,
        "token_attribution": tok_attr, "token_estimator_id": "chars4-v1", "success": 1,
        "outcome": "success", "wall_time_ns": None, "schema_version": "0.3.0"})


# --------------------------------------------------------------------- arm → env
def test_arm_env_wiring():
    a = ARMS["A_native"].env(live_cas_db="/l", decision_log="/d")
    assert "CR_REDUCE_MODE" not in a and "CR_GRAPH_DB" not in a         # observe, no reduction
    b = ARMS["B_shipped"].env(live_cas_db="/l", decision_log="/d")
    assert b["CR_REDUCE_MODE"] == "enforce" and b["CR_REDUCE_BUDGET"] == "256"
    assert b["CR_REDUCE_FLOOR"] == "400" and "CR_GRAPH_DB" not in b
    bt = ARMS["B_tuned"].env(live_cas_db="/l", decision_log="/d")
    assert bt["CR_REDUCE_BUDGET"] == "64"
    c = ARMS["C_graph"].env(live_cas_db="/l", decision_log="/d",
                            graph_db="/g", repo_id="repo", journal_db="/j")
    assert c["CR_REDUCE_MODE"] == "enforce" and c["CR_GRAPH_DB"] == "/g" and c["CR_REPO_ID"] == "repo"


def test_c_graph_env_requires_graph_inputs():
    import pytest
    with pytest.raises(ValueError):
        ARMS["C_graph"].env(live_cas_db="/l", decision_log="/d")       # missing graph_db/repo/journal


# --------------------------------------------------------------------- task selection
def test_select_search_heavy_tasks(tmp_path):
    replay = tmp_path / "replay.json"
    json.dump({"per_run": [
        {"run": "run-01", "task_id": "t1", "stratum": "fs1", "search_bucket_tokens": 100},
        {"run": "run-02", "task_id": "t2", "stratum": "fs2", "search_bucket_tokens": 5000},
        {"run": "run-03", "task_id": "t3", "stratum": "fs3", "search_bucket_tokens": 0},
        {"run": "run-04", "task_id": "t4", "stratum": "fs4", "search_bucket_tokens": 2000},
    ]}, open(replay, "w"))
    picks = select_search_heavy_tasks(str(replay), k=4)
    assert [p["run"] for p in picks] == ["run-02", "run-04", "run-01"]  # sorted desc, zero dropped


# --------------------------------------------------------------------- per-run metrics
def _journal(tmp_path, name, builder):
    db = str(tmp_path / name)
    j = HookJournal(db)
    builder(j)
    j.commit(); j.close()
    return db


def test_run_metrics_from_journal_and_decision_log(tmp_path):
    def build(j):
        _ev(j, eid="s1", kind="read", step=0, path="/*.py", tok=500, tok_attr="attributed", representation="search")
        _ev(j, eid="s2", kind="read", step=3, path="/*.py", tok=500, tok_attr="attributed", representation="search")  # repeat scope
        _ev(j, eid="f1", kind="read", step=1, path="/a.py", tok=100, tok_attr="attributed")
    jdb = _journal(tmp_path, "j.db", build)
    dlog = tmp_path / "d.jsonl"
    with open(dlog, "w") as fh:
        fh.write(json.dumps({"enforced": True, "saved_tokens": 300, "graph_ranked": False}) + "\n")
        fh.write(json.dumps({"enforced": False, "reason": "non_beneficial", "saved_tokens": 0}) + "\n")
    m = run_metrics(jdb, str(dlog))
    assert m["total_read_tokens"] == 1100 and m["search_reads"] == 2
    assert m["re_searches"] == 1                                        # /*.py materialized twice
    assert m["candidates_seen"] == 2 and m["reductions_enforced"] == 1 and m["non_beneficial"] == 1
    assert m["saved_tokens"] == 300


# --------------------------------------------------------------------- experiment deltas
def test_compare_computes_reduction_and_quality_deltas():
    runs = {
        "A_native":  {"total_read_tokens": 2000, "re_searches": 3, "task_resolved": True},
        "B_shipped": {"total_read_tokens": 1200, "re_searches": 3, "task_resolved": True},
        "B_tuned":   {"total_read_tokens": 900,  "re_searches": 4, "task_resolved": True},
        "C_graph":   {"total_read_tokens": 1200, "re_searches": 1, "task_resolved": True},
    }
    cmp = compare(runs)
    assert cmp["delta_tokens_B_shipped_minus_A"]["token_reduction"] == 800
    assert cmp["delta_tokens_B_shipped_minus_A"]["reduction_frac"] == 0.4
    assert cmp["delta_tokens_B_tuned_minus_A"]["token_reduction"] == 1100
    q = cmp["delta_quality_C_minus_B"]
    assert q["token_delta"] == 0                                        # token-neutral, as designed
    assert q["re_search_delta"] == -2                                   # graph → fewer re-searches


# --------------------------------------------------------------------- hook non_beneficial log
def test_hook_logs_non_beneficial_decision(tmp_path, monkeypatch):
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v,
                   "CR_DB": str(tmp_path / "live.db"),
                   "CR_DECISION_LOG": str(tmp_path / "d.jsonl")}.items():
        monkeypatch.setenv(k, val)
    raw = "\n".join(f"grep: src/very/long/path/to/module_{i}.py: Permission denied" for i in range(45))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "tool_response": raw})))
    assert hook_mod.main() == 0
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["reason"] == "non_beneficial" and rec["enforced"] is False
    assert rec["effective_tokens"] == rec["raw_tokens"] and rec["saved_tokens"] == 0
