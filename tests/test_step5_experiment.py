"""Step-5 A/B/C harness — deterministic tests (no quota).

Covers arm→env (no version override), arm-specific settings with TRUE B/C graph isolation, the
execution bridge through a fake `claude` process, effective-token / wall-time / fingerprint metrics
from real journal+decision-log fixtures, and the two experiment deltas incl. C-validity.
"""
import io
import json
import os
import stat

import pytest

from contextruntime import doctor
from contextruntime.corpusrunner import ClaudeBackend, RunSpec
from contextruntime.hookjournal import HookJournal
from contextruntime.reducers import hook as hook_mod

from corpus.step5_experiment import (ARMS, arm_reducer_cmd, build_arm_settings, compare,
                                     run_arm, run_metrics, select_search_heavy_tasks)


def _ev(j, *, eid, kind, step, path, tok=None, tok_attr=None, representation="file"):
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": "s1",
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": eid, "tool_name": "Read", "kind": kind,
        "channel": "native_read", "mutation_source": None, "mutation_status": None,
        "representation": representation, "path_absolute": path, "path_normalized": path,
        "repo_relative": None, "repo_id": None, "pre_version": None, "post_version": None,
        "content_version": None, "version_status": "stable", "response_hash": None,
        "model_visible_chars": None, "model_visible_tokens": tok,
        "token_status": ("text" if tok_attr == "attributed" else None), "token_attribution": tok_attr,
        "token_estimator_id": "chars4-v1", "success": 1, "outcome": "success",
        "wall_time_ns": None, "schema_version": "0.3.0"})


def _journal(tmp_path, name, builder):
    db = str(tmp_path / name)
    j = HookJournal(db); builder(j); j.commit(); j.close()
    return db


# --------------------------------------------------------------------- arm → env (no version override)
def test_arm_env_never_sets_live_client_version():
    for name in ARMS:
        e = ARMS[name].env(live_cas_db="/l", decision_log="/d",
                           graph_db="/g", repo_id="r", journal_db="/j")
        assert "CR_LIVE_CLIENT_VERSION" not in e     # must NOT override the fail-safe live probe


def test_arm_env_wiring_and_graph_gating():
    a = ARMS["A_native"].env(live_cas_db="/l", decision_log="/d")
    assert "CR_REDUCE_MODE" not in a and "CR_GRAPH_DB" not in a          # observe, no reduction
    b = ARMS["B_shipped"].env(live_cas_db="/l", decision_log="/d")
    assert b["CR_REDUCE_MODE"] == "enforce" and b["CR_REDUCE_BUDGET"] == "256"
    assert b["CR_REDUCE_FLOOR"] == "400" and "CR_GRAPH_DB" not in b       # B: graph OFF
    c = ARMS["C_graph"].env(live_cas_db="/l", decision_log="/d",
                            graph_db="/g", repo_id="repo", journal_db="/j")
    assert c["CR_GRAPH_DB"] == "/g"                                       # C: graph ON
    with pytest.raises(ValueError):
        ARMS["C_graph"].env(live_cas_db="/l", decision_log="/d")          # C needs graph inputs


# --------------------------------------------------------------------- arm settings: true B/C isolation
def test_build_arm_settings_b_cannot_invoke_graph_c_can(tmp_path):
    common = dict(journal_db="/j.db", live_cas_db="/l.db", decision_log="/d.jsonl",
                  graph_db="/g.db", repo_id="repo")
    bset = build_arm_settings(ARMS["B_shipped"], **common)
    cset = build_arm_settings(ARMS["C_graph"], **common)

    def reducer_cmd(settings):
        grp = [g for g in settings["hooks"]["PostToolUse"]
               if "reducers.hook" in g["hooks"][0]["command"]]
        assert len(grp) == 1
        return grp[0]["hooks"][0]["command"]

    b_cmd, c_cmd = reducer_cmd(bset), reducer_cmd(cset)
    assert "CR_GRAPH_DB" not in b_cmd and "CR_REDUCE_MODE=enforce" in b_cmd   # B: graph impossible
    assert "CR_GRAPH_DB=" in c_cmd and "CR_REPO_ID=" in c_cmd                 # C: graph wired
    assert "CR_LIVE_CLIENT_VERSION" not in b_cmd and "CR_LIVE_CLIENT_VERSION" not in c_cmd
    # observation cr-hook is present in BOTH arms (equal instrumentation)
    assert any("cr-hook" in g["hooks"][0]["command"] for g in bset["hooks"]["PostToolUse"])


# --------------------------------------------------------------------- execution bridge (fake claude)
_FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
args = sys.argv[1:]
rec = {"argv": args}
if "--settings" in args:
    rec["settings"] = json.load(open(args[args.index("--settings") + 1]))
json.dump(rec, open(os.environ["CLAUDE_CAPTURE"], "w"))
"""


def _spec():
    return RunSpec(run_order=1, task_id="t", category="c", base_commit="HEAD", repo_id="repo",
                   spec_path="", spec_sha256="", problem_statement="fix the issue", budget="")


def test_run_arm_binds_env_to_the_claude_process(tmp_path, monkeypatch):
    bind = tmp_path / "bin"; bind.mkdir()
    fake = bind / "claude"; fake.write_text(_FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bind}:{os.environ['PATH']}")
    capture = tmp_path / "cap.json"; monkeypatch.setenv("CLAUDE_CAPTURE", str(capture))
    backend = ClaudeBackend(client_version="test", clock=lambda: 0.0)   # skip real `claude --version`

    for arm_name, expect_graph in (("B_shipped", False), ("C_graph", True)):
        run_arm(ARMS[arm_name], backend, worktree=str(tmp_path), spec=_spec(),
                run_dir=str(tmp_path / arm_name), journal_db=str(tmp_path / "j.db"),
                live_cas_db=str(tmp_path / "l.db"), decision_log=str(tmp_path / "d.jsonl"),
                graph_db=str(tmp_path / "g.db"), repo_id="repo")
        rec = json.load(open(capture))
        assert "--settings" in rec["argv"]                                  # claude actually got it
        cmd = [g["hooks"][0]["command"] for g in rec["settings"]["hooks"]["PostToolUse"]
               if "reducers.hook" in g["hooks"][0]["command"]][0]
        assert ("CR_GRAPH_DB=" in cmd) is expect_graph                      # env reached the process


# --------------------------------------------------------------------- task selection
def test_select_search_heavy_tasks(tmp_path):
    replay = tmp_path / "replay.json"
    json.dump({"per_run": [
        {"run": "run-01", "task_id": "t1", "stratum": "fs1", "search_bucket_tokens": 100},
        {"run": "run-02", "task_id": "t2", "stratum": "fs2", "search_bucket_tokens": 5000},
        {"run": "run-03", "task_id": "t3", "stratum": "fs3", "search_bucket_tokens": 0},
        {"run": "run-04", "task_id": "t4", "stratum": "fs4", "search_bucket_tokens": 2000},
    ]}, open(replay, "w"))
    assert [p["run"] for p in select_search_heavy_tasks(str(replay), k=4)] == ["run-02", "run-04", "run-01"]


# --------------------------------------------------------------------- metrics (effective / wall / fp)
def test_run_metrics_effective_tokens_walltime_and_fingerprint(tmp_path):
    jdb = _journal(tmp_path, "j.db", lambda j: (
        _ev(j, eid="s1", kind="read", step=0, path="/*.py", tok=500, tok_attr="attributed", representation="search"),
        _ev(j, eid="f1", kind="read", step=1, path="/a.py", tok=100, tok_attr="attributed")))
    dlog = tmp_path / "d.jsonl"
    with open(dlog, "w") as fh:
        fh.write(json.dumps({"enforced": True, "saved_tokens": 300, "graph_ranked": False, "fingerprint": "abc"}) + "\n")
        fh.write(json.dumps({"enforced": True, "saved_tokens": 200, "graph_ranked": False, "fingerprint": "abc"}) + "\n")
        fh.write(json.dumps({"enforced": False, "reason": "non_beneficial", "fingerprint": "xyz"}) + "\n")
    rundir = tmp_path / "rd"; rundir.mkdir()
    json.dump({"budget_walltime": 12.3}, open(rundir / "agent-result.json", "w"))

    m = run_metrics(jdb, str(dlog), run_dir=str(rundir))
    assert m["journal_raw_read_tokens"] == 600 and m["reducer_saved_tokens"] == 500
    assert m["effective_read_tokens"] == 100                              # 600 raw − 500 saved
    assert m["re_search_fingerprint"] == 1                                # "abc" seen twice
    assert m["reductions_enforced"] == 2 and m["non_beneficial"] == 1 and m["wall_time_s"] == 12.3


# --------------------------------------------------------------------- experiment deltas + C validity
def test_compare_reduction_quality_and_c_validity():
    runs = {
        "A_native":  {"effective_read_tokens": 2000, "re_search_fingerprint": 3, "re_search_scope_proxy": 3, "wall_time_s": 10, "task_resolved": True},
        "B_shipped": {"effective_read_tokens": 1200, "re_search_fingerprint": 3, "re_search_scope_proxy": 3, "wall_time_s": 11, "task_resolved": True, "graph_ranked": 0, "reductions_enforced": 5},
        "C_graph":   {"effective_read_tokens": 1200, "re_search_fingerprint": 1, "re_search_scope_proxy": 2, "wall_time_s": 12, "task_resolved": True, "graph_ranked": 5, "reductions_enforced": 5},
    }
    cmp = compare(runs)
    d = cmp["delta_tokens_B_shipped_minus_A"]
    assert d["token_reduction"] == 800 and d["reduction_frac"] == 0.4 and d["wall_time_ratio"] == 1.1
    q = cmp["delta_quality_C_minus_B"]
    assert q["effective_token_delta"] == 0 and q["re_search_fingerprint_delta"] == -2
    assert cmp["validity"]["c_graph_engaged"] is True

    # a broken graph → C collapses into B → flagged invalid
    runs["C_graph"] = {**runs["C_graph"], "graph_ranked": 0}
    v = compare(runs)["validity"]
    assert v["c_graph_engaged"] is False and "never engaged" in v["c_graph_warning"]


# --------------------------------------------------------------------- hook fingerprint in the log
def test_hook_logs_fingerprint(tmp_path, monkeypatch):
    (v,) = tuple(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS)
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": v,
                   "CR_DB": str(tmp_path / "live.db"), "CR_DECISION_LOG": str(tmp_path / "d.jsonl")}.items():
        monkeypatch.setenv(k, val)
    raw = "\n".join(f"src/f{i}.py:{i}: match_{i}" for i in range(300))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "match"}, "tool_response": raw})))
    assert hook_mod.main() == 0
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert len(rec["fingerprint"]) == 16                                  # privacy-safe hash present
