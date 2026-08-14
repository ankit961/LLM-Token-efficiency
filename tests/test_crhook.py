"""Phase 2.4-C -- the cr-hook stdin entry point (the live-capture belt around HookJournal).

Each hook delivery is a SEPARATE process, so cross-delivery state must survive through the journal
DB, not a Python object. These tests drive crhook.run() repeatedly against a FILE-backed journal
(each call opens+closes its own connection, exactly like a fresh process would) and prove the whole
Pre->Post->Batch chain reconstructs. One test shells out to the real CLI to prove the argv/stdin
wiring. cr-hook is OBSERVE-ONLY and FAIL-OPEN: every call must exit 0, even on garbage input.
"""
import json
import subprocess
import sys

from contextruntime import crhook
from contextruntime.hookjournal import HookJournal


def _deliver(db, ev):
    # a fresh crhook.run() per event == a fresh hook process per delivery
    assert crhook.run(json.dumps(ev), db) == 0


def test_cr_hook_reconstructs_a_read_across_separate_deliveries(tmp_path):
    db = str(tmp_path / "journal.db")
    f = tmp_path / "a.py"
    f.write_text("def hello():\n    return 42\n")
    _deliver(db, {"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _deliver(db, {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(tmp_path),
                  "tool_use_id": "t1", "tool_name": "Read", "tool_input": {"file_path": str(f)}})
    _deliver(db, {"hook_event_name": "PostToolUse", "session_id": "s", "tool_use_id": "t1",
                  "tool_name": "Read", "tool_response": {"ok": True}})
    _deliver(db, {"hook_event_name": "PostToolBatch", "session_id": "s", "prompt_id": "p1",
                  "tool_calls": [{"tool_use_id": "t1", "tool_name": "Read", "tool_response": "x" * 400}]})
    j = HookJournal(db)
    rows = j.tool_events()
    assert len(rows) == 1 and rows[0]["kind"] == "read"
    assert rows[0]["version_status"] == "stable"           # real fd-hash, file unchanged pre->post
    assert rows[0]["model_visible_tokens"] == 100          # attributed at the batch (400/4)
    assert rows[0]["response_hash"] is not None            # authoritative hash from the batch payload
    assert j.session_state("s:main")[1] == 2               # UserPromptSubmit +1, PostToolBatch +1
    assert j.capture_stats()["errors"] == 0
    j.close()


def test_cr_hook_is_fail_open_on_garbage(tmp_path):
    db = str(tmp_path / "journal.db")
    for bad in ["", "   ", "not json{", "[1,2,3]", "42", "null", '"a string"']:
        assert crhook.run(bad, db) == 0                    # never raises, never non-zero
    # a well-formed but unknown event is silently ignored, not an error
    assert crhook.run(json.dumps({"hook_event_name": "TeammateIdle", "session_id": "s"}), db) == 0
    j = HookJournal(db)
    assert j.tool_events() == [] and j.capture_stats()["errors"] == 0
    j.close()


def test_cr_hook_memory_db_needs_no_directory():
    # ":memory:" must not trip the makedirs(parent) path
    assert crhook.run(json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}), ":memory:") == 0


def test_cr_hook_cli_reads_stdin_and_exits_zero(tmp_path):
    db = str(tmp_path / "journal.db")
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    r = subprocess.run([sys.executable, "-m", "contextruntime.cli", "cr-hook", "--db", db],
                       input=payload, capture_output=True, text=True)
    assert r.returncode == 0
    j = HookJournal(db)
    assert j.session_state("s:main")[1] == 1               # the UserPromptSubmit advanced the step
    j.close()
