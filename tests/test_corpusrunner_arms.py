"""Semantic Admission Experiment v1 -- the arm abstraction on ClaudeBackend.

No real `claude` process is ever invoked here (subprocess.run is monkeypatched) -- these tests
verify the ARGV CONSTRUCTION contract that the protocol depends on:
  - native (Arm A) carries no --mcp-config / --append-system-prompt (byte-identical to before
    the arm parameter existed, so already-graded Arm A runs remain valid).
  - semantic_directive (Arm B) adds --mcp-config + a directive --append-system-prompt, but the
    TASK PROMPT itself (the -p argument) is IDENTICAL to native for the same spec -- only the
    steering is additive, and native tools stay fully available (nothing is disabled).
  - semantic_enforced is RESERVED: constructing it is fine, but .run() raises NotImplementedError
    with ZERO subprocess calls -- it must be impossible to accidentally run Arm C.
"""
import json
import os

import pytest

from contextruntime.corpusrunner import ARMS, ClaudeBackend, RunSpec
from contextruntime.store import GraphStore


def _spec(**over):
    base = dict(run_order=1, task_id="demo__demo-1", category="fs1_oneline_1f_le3",
               base_commit="deadbeef", repo_id="demo", spec_path="spec.md",
               spec_sha256="sha256:x", problem_statement="Fix the thing.", budget="900s")
    base.update(over)
    return RunSpec(**base)


def _indexed_graph(path, n_defs=2):
    g = GraphStore(path)
    for i in range(n_defs):
        g.conn.execute(
            "INSERT INTO symbols(symbol_id,repo_id,language,kind,qualified_name,path,parser,"
            "resolution_quality,schema_version) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"demo::m.py::f{i}", "demo", "python", "function", f"f{i}", "m.py",
             "python_ast", 0.9, "0.10.0"))
    g.conn.commit(); g.close()


class _FakeProc:
    def __init__(self, argv, stdout="", returncode=0):
        self.argv = argv; self.stdout = stdout; self.stderr = ""; self.returncode = returncode


def _capturing_backend(monkeypatch, **kwargs):
    """A ClaudeBackend whose subprocess.run calls are captured instead of executed."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "git":
            return _FakeProc(argv, stdout="--- a/x\n+++ b/x\n")
        return _FakeProc(argv, stdout="DONE\n")

    monkeypatch.setattr("contextruntime.corpusrunner.subprocess.run", fake_run)
    backend = ClaudeBackend(client_version="test-1", clock=lambda it=iter(range(10)): next(it), **kwargs)
    return backend, calls


def _claude_call(calls):
    return next(c for c in calls if c and c[0] == "claude")


# --------------------------------------------------------------------------- arm validation
def test_arms_tuple_has_exactly_the_three_protocol_conditions():
    assert ARMS == ("native", "semantic_directive", "semantic_enforced")


def test_unknown_arm_rejected_at_construction():
    with pytest.raises(ValueError):
        ClaudeBackend(arm="bogus")


def test_semantic_directive_requires_codegraph_db_at_construction():
    with pytest.raises(ValueError):
        ClaudeBackend(arm="semantic_directive")               # no codegraph_db -> fail fast


# --------------------------------------------------------------------------- native (Arm A)
def test_native_argv_has_no_mcp_or_steering(monkeypatch, tmp_path):
    backend, calls = _capturing_backend(monkeypatch, arm="native")
    settings = str(tmp_path / "run-01" / "cr-hook-settings.json")
    os.makedirs(os.path.dirname(settings))
    backend.run(str(tmp_path / "wt"), _spec(), str(tmp_path / "j.db"), settings)
    argv = _claude_call(calls)
    assert "--mcp-config" not in argv and "--append-system-prompt" not in argv


# --------------------------------------------------------------------------- semantic_directive (Arm B)
def test_semantic_directive_adds_mcp_config_and_directive_brief(monkeypatch, tmp_path):
    graph = str(tmp_path / "graph.db")
    _indexed_graph(graph)
    backend, calls = _capturing_backend(monkeypatch, arm="semantic_directive",
                                        codegraph_db=graph, repo_id="demo")
    run_dir = tmp_path / "run-01"
    run_dir.mkdir()
    settings = str(run_dir / "cr-hook-settings.json")
    backend.run(str(tmp_path / "wt"), _spec(), str(tmp_path / "j.db"), settings)
    argv = _claude_call(calls)

    assert "--mcp-config" in argv
    mcp_path = argv[argv.index("--mcp-config") + 1]
    assert os.path.exists(mcp_path)
    cfg = json.load(open(mcp_path))
    assert "contextruntime" in cfg["mcpServers"]

    assert "--append-system-prompt" in argv
    brief = argv[argv.index("--append-system-prompt") + 1]
    assert "read_symbol" in brief and brief.strip() != ""


def test_semantic_directive_task_prompt_identical_to_native(monkeypatch, tmp_path):
    spec = _spec()
    graph = str(tmp_path / "graph.db")
    _indexed_graph(graph)

    native, native_calls = _capturing_backend(monkeypatch, arm="native")
    run_a = tmp_path / "a"; run_a.mkdir()
    native.run(str(tmp_path / "wt-a"), spec, str(tmp_path / "ja.db"), str(run_a / "s.json"))
    prompt_a = _claude_call(native_calls)[_claude_call(native_calls).index("-p") + 1]

    directive, directive_calls = _capturing_backend(monkeypatch, arm="semantic_directive",
                                                     codegraph_db=graph, repo_id="demo")
    run_b = tmp_path / "b"; run_b.mkdir()
    directive.run(str(tmp_path / "wt-b"), spec, str(tmp_path / "jb.db"), str(run_b / "s.json"))
    prompt_b = _claude_call(directive_calls)[_claude_call(directive_calls).index("-p") + 1]

    assert prompt_a == prompt_b                                # ONLY steering flags may differ


def test_semantic_directive_omits_append_system_prompt_when_brief_is_empty(monkeypatch, tmp_path):
    graph = str(tmp_path / "empty_graph.db")
    GraphStore(graph).close()                                   # indexed but zero symbols -> empty brief
    backend, calls = _capturing_backend(monkeypatch, arm="semantic_directive",
                                        codegraph_db=graph, repo_id="demo")
    run_dir = tmp_path / "run-01"; run_dir.mkdir()
    backend.run(str(tmp_path / "wt"), _spec(), str(tmp_path / "j.db"), str(run_dir / "s.json"))
    argv = _claude_call(calls)
    assert "--mcp-config" in argv                                # MCP still enabled (arm B's defining trait)
    assert "--append-system-prompt" not in argv                  # nothing to steer with -> omitted, not empty-strung


def test_agent_result_and_manifest_field_stamp_the_arm(monkeypatch, tmp_path):
    backend, _ = _capturing_backend(monkeypatch, arm="native")
    run_dir = tmp_path / "run-01"; run_dir.mkdir()
    res = backend.run(str(tmp_path / "wt"), _spec(), str(tmp_path / "j.db"), str(run_dir / "s.json"))
    assert res.arm == "native" and res.result["arm"] == "native"


# --------------------------------------------------------------------------- semantic_enforced (RESERVED)
def test_semantic_enforced_constructs_but_never_runs(monkeypatch, tmp_path):
    backend, calls = _capturing_backend(monkeypatch, arm="semantic_enforced")   # construction is fine
    with pytest.raises(NotImplementedError):
        backend.run(str(tmp_path / "wt"), _spec(), str(tmp_path / "j.db"), str(tmp_path / "s.json"))
    assert calls == []                                            # zero subprocess calls -- no side effects
