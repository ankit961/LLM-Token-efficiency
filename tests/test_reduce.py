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


# A search/listing payload above the reduce floor — the ONLY thing B1.0's gate touches.
GREP_RAW = "\n".join(f"src/f{i}.py:{i}: def handler_{i}(): match" for i in range(200))
CONFIRMED = "2.1.229"       # a client version in doctor's output-replacement allowlist


def test_hook_observe_by_default_on_search(monkeypatch, capsys):
    event = {"tool_name": "Grep", "tool_input": {"pattern": "handler"},
             "tool_response": GREP_RAW}
    rc, out = _run_hook(event, {}, monkeypatch, capsys)      # observe: no enforce
    assert rc == 0
    assert out.out.strip() == "{}"                          # nothing replaced in observe mode
    assert "would save" in out.err                          # but it reports the saving


def test_hook_enforce_replaces_and_handle_recovers(monkeypatch, capsys, tmp_path):
    """Enforce on a confirmed version replaces the output AND the emitted handle is
    genuinely recoverable from the live CAS (safety invariant #2)."""
    from contextruntime.reducers import livecas
    db = str(tmp_path / "live.db")
    event = {"tool_name": "Bash", "tool_input": {"command": "grep -rn handler src/"},
             "tool_response": {"stdout": GREP_RAW, "exitCode": 0}}
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": CONFIRMED,
                                "CR_DB": db, "CR_DECISION_LOG": str(tmp_path / "d.jsonl")},
                        monkeypatch, capsys)
    assert rc == 0
    new = json.loads(out.out)["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(new, dict) and new["exitCode"] == 0            # shape preserved
    assert len(new["stdout"]) < len(GREP_RAW)                        # actually shrank
    handle = new["stdout"].splitlines()[-1]
    assert "result://" in handle
    # the handle the model was handed resolves back to the full raw payload
    h = handle.split("result://")[1].strip(" []")
    rec = livecas.resolve(f"result://{h}", path=db)
    assert rec.found and "handler_199" in rec.text                   # tail of raw recovered


def test_hook_version_gate_fails_safe_on_unknown_version(monkeypatch, capsys, tmp_path):
    event = {"tool_name": "Grep", "tool_input": {"pattern": "handler"},
             "tool_response": GREP_RAW}
    # the LIVE version (authoritative) is unconfirmed — overrides the confirmed autouse default
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce",
                                "CR_LIVE_CLIENT_VERSION": "9.9.9-unconfirmed",
                                "CR_CLIENT_VERSION": "9.9.9-unconfirmed",
                                "CR_DB": str(tmp_path / "live.db"),
                                "CR_DECISION_LOG": str(tmp_path / "d.jsonl")}, monkeypatch, capsys)
    assert rc == 0
    assert out.out.strip() == "{}"                          # unknown live version → pass through raw
    assert "not confirmed" in out.err


def test_hook_execution_bash_passes_through(monkeypatch, capsys):
    """v0.1 narrowing: a test/execution Bash line is NOT a search/listing read — untouched."""
    event = {"tool_name": "Bash", "tool_input": {"command": "pytest"},
             "tool_response": {"stdout": PYTEST_RAW, "exitCode": 1}}
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": CONFIRMED},
                        monkeypatch, capsys)
    assert rc == 0
    assert out.out.strip() == "{}"                          # execution passes through


def test_hook_never_touches_source_read(monkeypatch, capsys):
    event = {"tool_name": "Read", "tool_input": {"file_path": "/x.py"},
             "tool_response": "def f():\n    pass\n" * 500}
    rc, out = _run_hook(event, {"CR_REDUCE_MODE": "enforce", "CR_CLIENT_VERSION": CONFIRMED},
                        monkeypatch, capsys)
    assert rc == 0
    assert out.out.strip() == "{}"                      # Read left native (C10)
