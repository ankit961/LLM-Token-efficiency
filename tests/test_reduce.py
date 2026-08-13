"""Phase 1 — ContextReduce tests: reducer invariants, retention, scan, hook."""
import io
import json
from pathlib import Path

import pytest

from contextruntime.reducers import base, library, planner
from contextruntime.reducers import hook as hook_mod
from contextruntime.residency import ingest_file
from contextruntime.store import GraphStore

FIX = Path(__file__).parent / "fixtures" / "synthetic_session.jsonl"

PYTEST_RAW = (
    "======= test session starts =======\n"
    + "tests/a.py " + "." * 2000 + " [ 80%]\n"     # a fat passing run (> reduce floor)
    + "tests/b.py ..F [100%]\n"
    "======= FAILURES =======\n"
    "    assert gw.charge_count == 1\n"
    "E   AssertionError: expected 1 charge, got 2\n"
    "tests/b.py:41: AssertionError\n"
    "FAILED tests/b.py::test_timeout_retry - AssertionError: expected 1 charge, got 2\n"
    "======= 1 failed, 240 passed in 3.2s =======\n"
)


def test_tests_reducer_preserves_failures_and_shrinks():
    out = library.reduce_tests(PYTEST_RAW, {})
    assert out.invariants_ok                        # the FAILED line survived
    assert "FAILED tests/b.py::test_timeout_retry" in out.reduced_text
    assert "1 failed, 240 passed" in out.reduced_text
    assert out.reduced_tokens < out.raw_tokens      # dots dropped
    assert out.ratio < 0.6
    assert out.handle.startswith("result://")


def test_grep_reducer_keeps_head_and_counts():
    raw = "\n".join(f"src/f{i}.py:{i}: match" for i in range(100))
    out = library.reduce_grep(raw, {})
    assert out.reduced_tokens < out.raw_tokens
    assert "80 more matches" in out.reduced_text     # 100 - 20 kept


def test_logs_reducer_keeps_errors():
    raw = "\n".join(["INFO ok"] * 300 + ["ERROR boom at line 5", "INFO done"])
    out = library.reduce_logs(raw, {})
    assert "ERROR boom at line 5" in out.reduced_text
    assert out.invariants_ok
    assert out.ratio < 0.5


def test_classify():
    assert library.classify("Bash", {"command": "pytest tests/"}, None) == "tests"
    assert library.classify("Grep", {}, None) == "grep"
    assert library.classify("Bash", {"command": "git diff"}, None) == "git"
    assert library.classify("Bash", {"command": "docker logs x"}, "log") == "logs"


def test_retention_never_reduces_source_reads():
    ok, reason = base.should_reduce("source_slice", 5000)
    assert not ok and "native" in reason
    ok, _ = base.should_reduce("test_result", 5000)
    assert ok
    ok, reason = base.should_reduce("log", 100)          # below MIN_REDUCE
    assert not ok


def test_scan_writes_reduces_edges_and_saves():
    store = GraphStore(":memory:")
    ingest_file(store, FIX)
    rep = planner.scan_graph(store)
    assert rep.reduced >= 1                              # the fat pytest log
    assert rep.saved_tokens > 0
    assert rep.ratio < 1.0
    assert rep.invariant_failures == 0
    assert store.edge_count("REDUCES") >= 1
    store.close()


def _run_hook(event, env, monkeypatch, capsys):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    rc = hook_mod.main()
    return rc, capsys.readouterr()


def test_hook_fail_open_observe_by_default(monkeypatch, capsys):
    event = {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"stdout": PYTEST_RAW, "exitCode": 1}}
    rc, out = _run_hook(event, {}, monkeypatch, capsys)
    assert rc == 0
    assert out.out.strip() == "{}"                      # no replacement in observe mode
    assert "would save" in out.err                      # but it reports the saving


def test_hook_enforce_replaces_preserving_shape(monkeypatch, capsys):
    event = {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"stdout": PYTEST_RAW, "exitCode": 1}}
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce",
                                "CR_OUTPUT_REPLACEMENT": "1"}, monkeypatch, capsys)
    assert rc == 0
    payload = json.loads(out.out)
    new = payload["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(new, dict) and new["exitCode"] == 1        # shape preserved
    assert "FAILED" in new["stdout"] and len(new["stdout"]) < len(PYTEST_RAW)


def test_hook_never_touches_source_read(monkeypatch, capsys):
    event = {"tool_name": "Read", "tool_input": {"file_path": "/x.py"},
             "tool_response": "def f():\n    pass\n" * 500}
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce",
                                "CR_OUTPUT_REPLACEMENT": "1"}, monkeypatch, capsys)
    assert rc == 0
    assert out.out.strip() == "{}"                      # Read left native (C10)
