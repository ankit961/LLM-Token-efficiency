"""Step-5 A/B/C harness — deterministic tests (Step-5.2). Zero quota.

Covers the token-accounting correction (journal model-visible IS the effective; saved is a
cross-check), sanitized arm envs (explicit modes, cleared version override), the version preflight,
the execution bridge + recovery MCP through a fake `claude`, graph provenance, the complete
Step5Runner, and metric/validity computation.
"""
import io
import json
import os
import stat
import subprocess

import pytest

from contextruntime import doctor
from contextruntime.corpusrunner import ClaudeBackend, MockAgentBackend, RunSpec
from contextruntime.hookjournal import HookJournal
from contextruntime.reducers import hook as hook_mod

from corpus.step5_experiment import (ARMS, ExperimentPreflightError, Step5Runner, arm_reducer_cmd,
                                     build_arm_settings, build_task_graph, compare,
                                     preflight_or_raise, run_arm, run_metrics,
                                     select_search_heavy_tasks, verify_graph_provenance,
                                     verify_token_accounting)

_CONFIRMED = next(iter(doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS))


def _ev(j, *, eid, kind, step, path, tok=None, tok_attr=None, representation="file", tuid=None):
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": "s1",
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": tuid or eid, "tool_name": "Read", "kind": kind,
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


# --------------------------------------------------------------------- arm env / command sanitization
def test_arm_env_explicit_modes_and_no_version():
    for name in ARMS:
        e = ARMS[name].env(live_cas_db="/l", decision_log="/d", graph_db="/g", repo_id="r", journal_db="/j")
        assert "CR_LIVE_CLIENT_VERSION" not in e
        assert e["CR_REDUCE_MODE"] in ("observe", "enforce") and e["CR_GRAPH_MODE"] in ("on", "off")
    assert ARMS["A_native"].env(live_cas_db="/l", decision_log="/d")["CR_REDUCE_MODE"] == "observe"
    assert ARMS["B_shipped"].env(live_cas_db="/l", decision_log="/d")["CR_GRAPH_MODE"] == "off"
    with pytest.raises(ValueError):
        ARMS["C_graph"].env(live_cas_db="/l", decision_log="/d")


def test_arm_reducer_cmd_clears_version_and_gates_graph():
    b = arm_reducer_cmd(ARMS["B_shipped"], live_cas_db="/l", decision_log="/d")
    c = arm_reducer_cmd(ARMS["C_graph"], live_cas_db="/l", decision_log="/d",
                        graph_db="/g", repo_id="r", journal_db="/j")
    assert b.startswith("env -u CR_LIVE_CLIENT_VERSION ")                 # inherited override cleared
    assert "CR_GRAPH_MODE=off" in b and "CR_GRAPH_DB" not in b            # B: graph impossible
    assert "CR_GRAPH_MODE=on" in c and "CR_GRAPH_DB=" in c                # C: graph wired


def test_build_arm_settings_isolation():
    common = dict(journal_db="/j.db", live_cas_db="/l.db", decision_log="/d.jsonl",
                  graph_db="/g.db", repo_id="repo")
    def rcmd(s):
        return [g["hooks"][0]["command"] for g in s["hooks"]["PostToolUse"]
                if "reducers.hook" in g["hooks"][0]["command"]][0]
    assert "CR_GRAPH_DB" not in rcmd(build_arm_settings(ARMS["B_shipped"], **common))
    assert "CR_GRAPH_DB=" in rcmd(build_arm_settings(ARMS["C_graph"], **common))


# --------------------------------------------------------------------- preflight
def test_preflight_raises_on_unconfirmed(monkeypatch):
    monkeypatch.setattr(doctor, "live_client_version", lambda **k: None)
    with pytest.raises(ExperimentPreflightError):
        preflight_or_raise()
    monkeypatch.setattr(doctor, "live_client_version", lambda **k: _CONFIRMED)
    assert preflight_or_raise() == _CONFIRMED


# --------------------------------------------------------------------- execution bridge (+ recovery MCP)
_FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, os, json
args = sys.argv[1:]
rec = {"argv": args}
if "--settings" in args:
    rec["settings"] = json.load(open(args[args.index("--settings") + 1]))
json.dump(rec, open(os.environ["CLAUDE_CAPTURE"], "w"))
"""


def _spec(base_commit="HEAD"):
    return RunSpec(run_order=1, task_id="t", category="c", base_commit=base_commit, repo_id="repo",
                   spec_path="", spec_sha256="", problem_statement="fix the issue", budget="")


def test_run_arm_binds_settings_and_recovery_mcp(tmp_path, monkeypatch):
    bind = tmp_path / "bin"; bind.mkdir()
    fake = bind / "claude"; fake.write_text(_FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bind}:{os.environ['PATH']}")
    cap = tmp_path / "cap.json"; monkeypatch.setenv("CLAUDE_CAPTURE", str(cap))
    backend = ClaudeBackend(client_version="test", clock=lambda: 0.0)

    for arm_name, expect_graph in (("B_shipped", False), ("C_graph", True)):
        run_arm(ARMS[arm_name], backend, worktree=str(tmp_path), spec=_spec(),
                run_dir=str(tmp_path / arm_name), journal_db=str(tmp_path / "j.db"),
                live_cas_db=str(tmp_path / "l.db"), decision_log=str(tmp_path / "d.jsonl"),
                graph_db=str(tmp_path / "g.db"), repo_id="repo")
        rec = json.load(open(cap))
        assert "--settings" in rec["argv"] and "--mcp-config" in rec["argv"]   # recovery callable
        cmd = [g["hooks"][0]["command"] for g in rec["settings"]["hooks"]["PostToolUse"]
               if "reducers.hook" in g["hooks"][0]["command"]][0]
        assert ("CR_GRAPH_DB=" in cmd) is expect_graph


# --------------------------------------------------------------------- task selection
def test_select_search_heavy_tasks(tmp_path):
    replay = tmp_path / "r.json"
    json.dump({"per_run": [
        {"run": "run-01", "task_id": "t1", "stratum": "fs1", "search_bucket_tokens": 100},
        {"run": "run-02", "task_id": "t2", "stratum": "fs2", "search_bucket_tokens": 5000},
        {"run": "run-03", "task_id": "t3", "stratum": "fs3", "search_bucket_tokens": 0},
    ]}, open(replay, "w"))
    assert [p["run"] for p in select_search_heavy_tasks(str(replay), k=3)] == ["run-02", "run-01"]


# --------------------------------------------------------------------- metrics: journal IS effective
def test_run_metrics_effective_is_journal_not_double_subtracted(tmp_path):
    jdb = _journal(tmp_path, "j.db", lambda j: (
        _ev(j, eid="s1", kind="read", step=0, path="/*.py", tok=500, tok_attr="attributed", representation="search"),
        _ev(j, eid="f1", kind="read", step=1, path="/a.py", tok=100, tok_attr="attributed")))
    dlog = tmp_path / "d.jsonl"
    with open(dlog, "w") as fh:
        fh.write(json.dumps({"enforced": True, "saved_tokens": 300, "fingerprint": "abc"}) + "\n")
        fh.write(json.dumps({"enforced": True, "saved_tokens": 200, "fingerprint": "abc"}) + "\n")
    json.dump({"budget_walltime": 9.5}, open(tmp_path / "manifest.json", "w"))
    m = run_metrics(jdb, str(dlog), manifest_json=str(tmp_path / "manifest.json"))
    assert m["effective_read_tokens"] == 600         # journal directly — NOT 600−500 (no double-count)
    assert m["reducer_saved_tokens"] == 500          # cross-check only
    assert m["exact_search_repeat_count"] == 1 and m["wall_time_s"] == 9.5


def test_verify_token_accounting_canary(tmp_path):
    jdb = _journal(tmp_path, "j.db", lambda j:
                   _ev(j, eid="r1", kind="read", step=0, path="/*.py", tok=250,
                       tok_attr="attributed", representation="search", tuid="U1"))
    dlog = tmp_path / "d.jsonl"
    open(dlog, "w").write(json.dumps({"enforced": True, "reduced_tokens": 244, "tool_use_id": "U1"}) + "\n")
    assert verify_token_accounting(jdb, str(dlog))["ok"] is True          # journal≈reduced ⇒ ok
    # a journal that still showed RAW would disagree
    jdb2 = _journal(tmp_path, "j2.db", lambda j:
                    _ev(j, eid="r1", kind="read", step=0, path="/*.py", tok=5000,
                        tok_attr="attributed", representation="search", tuid="U1"))
    assert verify_token_accounting(jdb2, str(dlog))["ok"] is False


# --------------------------------------------------------------------- deltas + C validity
def test_compare_and_c_validity():
    runs = {
        "A_native":  {"effective_read_tokens": 2000, "exact_search_repeat_count": 3, "repeated_scope_count": 3, "wall_time_s": 10, "task_resolved": True},
        "B_shipped": {"effective_read_tokens": 1200, "exact_search_repeat_count": 3, "repeated_scope_count": 3, "wall_time_s": 11, "task_resolved": True},
        "C_graph":   {"effective_read_tokens": 1200, "exact_search_repeat_count": 1, "repeated_scope_count": 2, "wall_time_s": 12, "task_resolved": True, "graph_ranked": 5, "reductions_enforced": 5},
    }
    cmp = compare(runs)
    assert cmp["delta_tokens_B_shipped_minus_A"]["token_reduction"] == 800
    assert cmp["delta_tokens_B_shipped_minus_A"]["reduction_frac"] == 0.4
    assert cmp["delta_quality_C_minus_B"]["exact_search_repeat_delta"] == -2
    assert cmp["validity"]["c_valid"] is True
    for broken in ({"graph_ranked": 0, "reductions_enforced": 5}, {"graph_ranked": 0, "reductions_enforced": 0}):
        runs["C_graph"] = {**runs["C_graph"], **broken}
        v = compare(runs)["validity"]
        assert v["c_valid"] is False and "did not test" in v["c_warning"]


# --------------------------------------------------------------------- graph provenance
def test_build_task_graph_and_provenance(tmp_path):
    wt = tmp_path / "wt"; wt.mkdir()
    (wt / "a.py").write_text("def foo():\n    return bar()\n")
    gdb = str(tmp_path / "g.db")
    prov = build_task_graph(str(wt), "sha-abc", gdb, repo_id="repo")
    assert prov["base_commit"] == "sha-abc" and prov["files_indexed"] >= 1
    assert verify_graph_provenance(gdb, "sha-abc") is True
    assert verify_graph_provenance(gdb, "sha-WRONG") is False


# --------------------------------------------------------------------- full controlled runner
def _git_repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir()
    def g(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (r / "a.py").write_text("def f():\n    return 1\n")
    g("add", "-A"); g("commit", "-q", "-m", "init")
    sha = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return str(r), sha


def test_step5runner_produces_immutable_artifacts(tmp_path):
    repo, sha = _git_repo(tmp_path)
    runner = Step5Runner(repo, MockAgentBackend())
    out = runner.run_task(_spec(base_commit=sha), ARMS["C_graph"], str(tmp_path / "run"))
    for name in ("journal.sqlite", "agent.patch", "agent-result.json", "evaluation.json",
                 "manifest.json", "hashes.json"):
        assert os.path.exists(os.path.join(out, name)), name
    man = json.load(open(os.path.join(out, "manifest.json")))
    assert man["arm"] == "C_graph" and man["base_commit"] == sha and "budget_walltime" in man
    assert man["graph_provenance"] and man["graph_provenance"]["base_commit"] == sha   # C graph bound
    with pytest.raises(FileExistsError):                                                # immutable dir
        runner.run_task(_spec(base_commit=sha), ARMS["A_native"], out)


# --------------------------------------------------------------------- hook: graph-mode gate + decision fields
def test_hook_graph_mode_off_disables_graph(monkeypatch):
    from contextruntime.reducers.gate import route
    monkeypatch.setenv("CR_GRAPH_MODE", "off")
    monkeypatch.setenv("CR_GRAPH_DB", "/whatever.db")
    monkeypatch.setenv("CR_REPO_ID", "repo"); monkeypatch.setenv("CR_JOURNAL_DB", "/j.db")
    assert hook_mod._graph_scores({"session_id": "s"}, "raw", route("Grep", {"pattern": "x"})) == {}


def test_hook_decision_has_tool_use_id_and_fingerprint(tmp_path, monkeypatch):
    for k, val in {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": _CONFIRMED,
                   "CR_DB": str(tmp_path / "live.db"), "CR_DECISION_LOG": str(tmp_path / "d.jsonl")}.items():
        monkeypatch.setenv(k, val)
    raw = "\n".join(f"src/f{i}.py:{i}: match_{i}" for i in range(300))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Grep", "tool_input": {"pattern": "match"}, "tool_response": raw, "tool_use_id": "U9"})))
    assert hook_mod.main() == 0
    rec = json.loads(open(tmp_path / "d.jsonl").read().splitlines()[-1])
    assert rec["tool_use_id"] == "U9" and len(rec["fingerprint"]) == 16
