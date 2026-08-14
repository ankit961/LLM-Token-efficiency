"""Phase 2.4-C -- HookJournal + HookCapture against Claude Code's REAL hook contract.

Payloads use `hook_event_name` (not `event`), `PostToolBatch.tool_calls` (each with the serialized
model-visible `tool_response`), `cwd`, and a (status, digest) hasher. Covers: step epoch, two-
snapshot version_status, mutation-as-state-transition, failed reads not materializing, path
normalization, per-call token attribution (incl. ambiguous multipath), batch idempotency, lineage
epochs on /clear, capture-error coverage, non-comparable hash states, and a CROSS-PROCESS Pre->Post.
"""
from contextruntime.classify import EDIT_PRECONDITION, UNKNOWN, classify_reads
from contextruntime.hookjournal import HookCapture, HookJournal
from contextruntime.normalize import to_events


def _hasher(versions):
    return lambda p: ("ok", versions[p]) if p in versions else ("absent", "absent:v1")


def _cap(versions):
    j = HookJournal(":memory:")
    return j, HookCapture(j, hasher=_hasher(versions))


def _pre(c, sid, tuid, tool, tinput, cwd="/repo", agent=None):
    c.on_event({"hook_event_name": "PreToolUse", "session_id": sid, "agent_id": agent, "cwd": cwd,
                "tool_use_id": tuid, "tool_name": tool, "tool_input": tinput})


def _post(c, sid, tuid, tool, success=True, agent=None):
    name = "PostToolUse" if success else "PostToolUseFailure"
    c.on_event({"hook_event_name": name, "session_id": sid, "agent_id": agent,
                "tool_use_id": tuid, "tool_name": tool, "tool_response": {"ok": True}})   # STRUCTURED


def _batch(c, sid, prompt, calls):
    c.on_event({"hook_event_name": "PostToolBatch", "session_id": sid, "prompt_id": prompt,
                "tool_calls": calls})


def test_dispatcher_reads_hook_event_name_not_event():
    j, c = _cap({})
    c.on_event({"event": "UserPromptSubmit", "session_id": "s"})     # FileChanged-style field -> ignored
    assert j.session_state("s:main") == (0, 0)
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    assert j.session_state("s:main")[1] == 1


def test_step_is_model_request_epoch():
    j, c = _cap({})
    c.on_event({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    assert j.session_state("s:main")[1] == 1
    _batch(c, "s", "p1", [])
    assert j.session_state("s:main")[1] == 2


def test_stable_read_then_edit_precondition_end_to_end():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read")
    _pre(c, "s", "t2", "Edit", {"file_path": "/repo/a.py"})
    versions["/repo/a.py"] = "v2"                                   # the edit changes the bytes
    _post(c, "s", "t2", "Edit")
    rows = j.tool_events("s:main:0")
    assert [r["kind"] for r in rows] == ["read", "edit"]
    assert rows[0]["version_status"] == "stable" and rows[0]["content_version"] == "v1"
    assert rows[1]["content_version"] == "v1"                       # edit PRE-version
    labels = classify_reads(to_events(rows))
    assert labels[rows[0]["event_id"]].observed_class == EDIT_PRECONDITION


def test_read_time_race_is_unknown():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    c.on_event({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": "/repo",
                "tool_use_id": "t1", "tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}})
    versions["/repo/a.py"] = "v2"                                   # changes DURING the read
    _post(c, "s", "t1", "Read")
    row = j.tool_events()[0]
    assert row["version_status"] == "raced" and row["content_version"] is None
    assert classify_reads(to_events([row]))[row["event_id"]].observed_class == UNKNOWN


def test_identical_write_is_not_a_mutation():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Write", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Write")
    assert j.tool_events() == []


def test_failed_read_is_not_a_materialization():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read", success=False)
    row = j.tool_events()[0]
    assert row["kind"] == "read" and row["success"] == 0
    assert to_events([row]) == []                                  # failed read -> no classify event


def test_failed_op_that_changed_bytes_is_a_mutation():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Edit", {"file_path": "/repo/a.py"})
    versions["/repo/a.py"] = "v2"
    _post(c, "s", "t1", "Edit", success=False)
    row = j.tool_events()[0]
    assert row["kind"] == "edit" and row["outcome"] == "failed_partial"


def test_path_normalized_links_bash_relative_to_native_absolute():
    versions = {"/repo/src/a.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Bash", {"command": "cat src/a.py"}, cwd="/repo"); _post(c, "s", "t1", "Bash")
    _pre(c, "s", "t2", "Edit", {"file_path": "/repo/src/a.py"}, cwd="/repo")
    versions["/repo/src/a.py"] = "v2"
    _post(c, "s", "t2", "Edit")
    rows = j.tool_events()
    assert rows[0]["path_normalized"] == rows[1]["path_normalized"] == "/repo/src/a.py"
    labels = classify_reads(to_events(rows))
    assert labels[rows[0]["event_id"]].observed_class == EDIT_PRECONDITION


def test_batch_attributes_model_visible_tokens_once():
    versions = {"/repo/a.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read")
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_name": "Read", "tool_response": "x" * 400}])
    row = j.tool_events()[0]
    assert row["token_attribution"] == "attributed" and row["model_visible_tokens"] == 100  # 400/4


def test_multipath_bash_tokens_are_ambiguous_not_double_counted():
    versions = {"/repo/a.py": "v1", "/repo/b.py": "v1"}
    j, c = _cap(versions)
    _pre(c, "s", "t1", "Bash", {"command": "cat a.py b.py"}, cwd="/repo"); _post(c, "s", "t1", "Bash")
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_name": "Bash", "tool_response": "x" * 400}])
    rows = j.tool_events()
    assert len(rows) == 2
    assert all(r["token_attribution"] == "ambiguous_multipath" and r["model_visible_tokens"] is None
               for r in rows)


def test_batch_is_idempotent():
    j, c = _cap({})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    calls = [{"tool_use_id": "t1", "tool_name": "Read", "tool_response": "x"}]
    _batch(c, "s", "p1", calls)
    _batch(c, "s", "p1", calls)                                     # re-delivered
    assert j.session_state("s:main")[1] == 2                        # advanced once, not twice


def test_clear_creates_a_new_lineage_epoch():
    j, c = _cap({})
    c.on_event({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"})
    assert j.session_state("s:main")[0] == 0
    c.on_event({"hook_event_name": "SessionStart", "session_id": "s", "source": "clear"})
    assert j.session_state("s:main")[0] == 1                        # /clear -> new lineage


def test_capture_error_is_logged_not_invisible():
    j, c = _cap({})
    c.on_event({"hook_event_name": "PostToolBatch", "session_id": "s", "tool_calls": "malformed"})
    cov = j.capture_coverage()
    assert cov["errors"] >= 1 and cov["coverage"] is not None


def test_unavailable_hash_is_not_stable():
    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=lambda p: ("unavailable", None))      # never comparable
    _pre(c, "s", "t1", "Read", {"file_path": "/x"}); _post(c, "s", "t1", "Read")
    row = j.tool_events()[0]
    assert row["version_status"] != "stable" and row["content_version"] is None


def test_pending_state_survives_a_process_boundary(tmp_path):
    # PreToolUse in one process, PostToolUse in another: pending pre-hash must be persisted, not
    # held in a Python object -- each hook delivery is a separate command-hook invocation.
    db = str(tmp_path / "hj.db")
    versions = {"/repo/a.py": "v1"}
    j1 = HookJournal(db)
    _pre(HookCapture(j1, _hasher(versions)), "s", "t1", "Read", {"file_path": "/repo/a.py"})
    j1.close()                                                     # "process A" exits
    j2 = HookJournal(db)                                           # "process B" -- fresh connection
    _post(HookCapture(j2, _hasher(versions)), "s", "t1", "Read")
    rows = j2.tool_events()
    assert len(rows) == 1 and rows[0]["kind"] == "read" and rows[0]["version_status"] == "stable"
    j2.close()
