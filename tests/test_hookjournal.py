"""Phase 2.4-C -- HookJournal + HookCapture (prospective observation layer).

Model-request-epoch step counter, two-snapshot version_status (stable/raced), mutation as an
observed state transition (pre != post, incl. failed-partial; identical write is NOT a mutation),
Bash channel, parallel-batch stamping, metadata-only privacy, and an end-to-end pass through the
normalizer into classify_reads.
"""
from contextruntime.classify import EDIT_PRECONDITION, UNKNOWN, classify_reads
from contextruntime.hookjournal import HookCapture, HookJournal
from contextruntime.normalize import to_events


def _cap(versions):
    j = HookJournal(":memory:")
    return j, HookCapture(j, hasher=lambda p: versions.get(p, "absent:v1"))


def _read(c, sid, tuid, path, resp="1\tx"):
    c.on_event({"event": "PreToolUse", "session_id": sid, "tool_use_id": tuid,
                "tool_name": "Read", "tool_input": {"file_path": path}})
    c.on_event({"event": "PostToolUse", "session_id": sid, "tool_use_id": tuid,
                "tool_name": "Read", "tool_response": resp})


def test_step_is_model_request_epoch():
    j, c = _cap({})
    c.on_event({"event": "SessionStart", "session_id": "s"})
    c.on_event({"event": "UserPromptSubmit", "session_id": "s"})
    assert c.step["s:main"] == 1                              # first model request
    c.on_event({"event": "PostToolBatch", "session_id": "s", "tool_use_ids": []})
    assert c.step["s:main"] == 2                              # advances after the batch resolves


def test_stable_read_then_edit_is_precondition_end_to_end():
    versions = {"/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "UserPromptSubmit", "session_id": "s"})
    _read(c, "s", "t1", "/a.py")                              # read the file at v1
    c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": "t2",
                "tool_name": "Edit", "tool_input": {"file_path": "/a.py"}})
    versions["/a.py"] = "v2"                                  # the edit changes the bytes
    c.on_event({"event": "PostToolUse", "session_id": "s", "tool_use_id": "t2",
                "tool_name": "Edit", "tool_response": "ok"})
    rows = j.tool_events("s:main")
    assert [r["kind"] for r in rows] == ["read", "edit"]
    assert rows[0]["version_status"] == "stable" and rows[0]["content_version"] == "v1"
    assert rows[1]["content_version"] == "v1"                 # edit's PRE-version
    labels = classify_reads(to_events(rows))
    assert labels[rows[0]["event_id"]].observed_class == EDIT_PRECONDITION


def test_read_time_race_becomes_unknown_end_to_end():
    versions = {"/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Read", "tool_input": {"file_path": "/a.py"}})
    versions["/a.py"] = "v2"                                  # file changes DURING the read
    c.on_event({"event": "PostToolUse", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Read", "tool_response": "x"})
    row = j.tool_events()[0]
    assert row["version_status"] == "raced" and row["content_version"] is None
    labels = classify_reads(to_events([row]))
    assert labels[row["event_id"]].observed_class == UNKNOWN


def test_identical_write_is_not_a_mutation():
    versions = {"/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Write", "tool_input": {"file_path": "/a.py"}})
    c.on_event({"event": "PostToolUse", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Write", "tool_response": "ok"})          # bytes unchanged
    assert j.tool_events() == []


def test_failed_op_that_changed_bytes_is_a_mutation():
    versions = {"/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Edit", "tool_input": {"file_path": "/a.py"}})
    versions["/a.py"] = "v2"
    c.on_event({"event": "PostToolUseFailure", "session_id": "s", "tool_use_id": "t1",
                "tool_name": "Edit", "tool_response": "err"})
    row = j.tool_events()[0]
    assert row["kind"] == "edit" and row["success"] == 0 and row["outcome"] == "failed_partial"


def test_bash_read_channel_and_batch_stamp():
    versions = {"a.py": "v1", "b.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "UserPromptSubmit", "session_id": "s"})
    for t, cmd in [("t1", "cat a.py"), ("t2", "cat b.py")]:
        c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": t,
                    "tool_name": "Bash", "tool_input": {"command": cmd}})
        c.on_event({"event": "PostToolUse", "session_id": "s", "tool_use_id": t,
                    "tool_name": "Bash", "tool_response": "contents"})
    c.on_event({"event": "PostToolBatch", "session_id": "s", "prompt_id": "p1",
                "tool_use_ids": ["t1", "t2"]})
    rows = j.tool_events()
    assert all(r["channel"] == "bash_materialization" for r in rows)
    assert all(r["batch_id"] is not None and r["batch_size"] == 2 and r["parallel"] == 1 for r in rows)


def test_journal_is_metadata_only():
    versions = {"/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"event": "PreToolUse", "session_id": "s", "tool_use_id": "t1", "tool_name": "Write",
                "tool_input": {"file_path": "/a.py", "content": "TOPSECRET"}})
    versions["/a.py"] = "v2"
    c.on_event({"event": "PostToolUse", "session_id": "s", "tool_use_id": "t1", "tool_name": "Write",
                "tool_response": "ok", "tool_input": {"content": "TOPSECRET"}})
    row = dict(j.tool_events()[0])
    assert "TOPSECRET" not in str(list(row.values()))         # raw content never persisted
    assert "content" not in row and "old_string" not in row and "command" not in row
