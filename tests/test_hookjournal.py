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
    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    c.on_event({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": "/repo",
                "tool_use_id": "t1", "tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}})
    st = j.capture_stats()
    assert st["errors"] >= 1 and st["delivery_success_ratio"] is not None


# Truthful coverage: SPLIT ledgers. bash_unknown_share is measured against BASH calls only (not
# diluted by always-recognized Read/Edit); pre_capture_rate is PreToolUse-seen / batch-resolved.
def test_capture_stats_split_denominators():
    j, c = _cap({"/repo/a.py": "v1", "/repo/b.py": "v1"})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Bash", {"command": "cat b.py"}, cwd="/repo"); _post(c, "s", "t1", "Bash")   # known
    _pre(c, "s", "t2", "Bash", {"command": "weird | pipeline"}, cwd="/repo"); _post(c, "s", "t2", "Bash")  # unknown
    _pre(c, "s", "t3", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t3", "Read")            # not Bash
    _batch(c, "s", "p1", [{"tool_use_id": t, "tool_response": "x"} for t in ("t1", "t2", "t3")])
    st = j.capture_stats()
    assert st["pre_tool_calls_seen"] == 3 and st["bash_calls_seen"] == 2 and st["unknown_bash_calls"] == 1
    # 1 unknown of 2 BASH calls -> 0.5, NOT 1/3 (the Read must not dilute shell-blindness)
    assert st["bash_unknown_share"] == 0.5
    assert st["batch_tool_calls_resolved"] == 3 and st["pre_capture_rate"] == 1.0


# A dropped PreToolUse shows up as pre_capture_rate < 1.0 (the batch is the authoritative count).
def test_pre_capture_rate_flags_a_missed_pretooluse():
    j, c = _cap({"/repo/a.py": "v1"})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read")   # only 1 Pre seen
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_response": "x"},
                          {"tool_use_id": "t2", "tool_response": "x"}])                    # batch says 2
    assert j.capture_stats()["pre_capture_rate"] == 0.5


# The AUTHORITATIVE response_hash comes from the PostToolBatch model-visible payload (a string or a
# text content-block array), NOT PostToolUse's structured tool_response -- so it must equal hash(that).
def test_response_hash_is_the_model_visible_batch_payload():
    from contextruntime.hookjournal import measure_model_visible_response as m
    j, c = _cap({"/repo/a.py": "v1"})
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read")
    row = j.tool_events()[0]
    assert row["response_hash"] is None                          # not set from the structured PostToolUse
    payload = [{"type": "text", "text": "hello world"}]
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_name": "Read", "tool_response": payload}])
    row = j.tool_events()[0]
    assert row["response_hash"] == m(payload)["hash"] and row["response_hash"] is not None


# FAIL-OPEN: even a broken journal (the ledger bump itself throws) must not let on_event raise.
def test_on_event_never_raises_even_if_the_ledger_bump_fails():
    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=lambda p: ("ok", "v1"))
    j.bump = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))   # fail before any savepoint
    c.on_event({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": "/repo",
                "tool_use_id": "t1", "tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}})
    # no exception escaped; the delivery simply produced nothing
    assert j.tool_events() == []


# ATOMIC delivery: a failure after pop_pending must roll back, leaving pending intact for retry.
def test_delivery_rollback_preserves_pending_on_failure():
    j = HookJournal(":memory:")
    calls = {"n": 0}

    def h(p):
        calls["n"] += 1
        if calls["n"] == 2:                                  # the post-hash, AFTER pop_pending
            raise RuntimeError("boom")
        return ("ok", "v1")

    c = HookCapture(j, hasher=h)
    _pre(c, "s", "t1", "Edit", {"file_path": "/repo/a.py"})
    _post(c, "s", "t1", "Edit")
    assert j.conn.execute("SELECT 1 FROM pending_tools WHERE tool_use_id='t1'").fetchone() is not None
    assert j.tool_events() == [] and j.capture_stats()["errors"] >= 1


# ...and a failure after claiming a batch must not leave the batch processed or advance the step.
def test_delivery_rollback_unclaims_batch_on_failure():
    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=lambda p: ("ok", "v1"))
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    j.stamp_batch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))   # fail right after claim
    c.on_event({"hook_event_name": "PostToolBatch", "session_id": "s", "prompt_id": "p1",
                "tool_calls": [{"tool_use_id": "t1", "tool_response": "x"}]})
    assert j.conn.execute("SELECT COUNT(*) c FROM processed_batches").fetchone()["c"] == 0
    assert j.session_state("s:main")[1] == 1                 # step not advanced


# MUTATION CERTAINTY: an unverified mutation (a hash unavailable) can never produce a precondition;
# a read crossing it is UNKNOWN.
def test_unverified_mutation_makes_crossing_read_unknown():
    versions = {"/repo/a.py": "v1"}

    def h(p):
        return ("ok", versions[p]) if p in versions else ("unavailable", None)

    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=h)
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Read", {"file_path": "/repo/a.py"}); _post(c, "s", "t1", "Read")
    _pre(c, "s", "t2", "Edit", {"file_path": "/repo/a.py"})
    del versions["/repo/a.py"]                               # post-hash unavailable -> unverified mutation
    _post(c, "s", "t2", "Edit")
    rows = j.tool_events("s:main:0")
    edit = [r for r in rows if r["kind"] == "edit"][0]
    read = [r for r in rows if r["kind"] == "read"][0]
    assert edit["mutation_status"] == "unverified"
    labels = classify_reads(to_events(rows))
    assert labels[read["event_id"]].observed_class == UNKNOWN


# GIT BLOB: version resolved at capture from the blob bytes (same digest namespace as the worktree),
# so a `git show` read that no longer matches the current file conflicts.
def test_git_blob_version_resolved_at_capture():
    versions = {"/repo/foo.py": "vNEW"}

    def h(p):
        return ("ok", versions[p]) if p in versions else ("absent", "absent:v1")

    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=h, git_blob_hasher=lambda cwd, ref, path: ("ok", "vOLD", "/repo/foo.py"))
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Bash", {"command": "git show HEAD~2:foo.py"}, cwd="/repo"); _post(c, "s", "t1", "Bash")
    _pre(c, "s", "t2", "Edit", {"file_path": "/repo/foo.py"}, cwd="/repo")
    versions["/repo/foo.py"] = "vNEWER"
    _post(c, "s", "t2", "Edit")
    rows = j.tool_events("s:main:0")
    read = [r for r in rows if r["kind"] == "read"][0]
    assert read["representation"] == "git_blob" and read["content_version"] == "vOLD"
    labels = classify_reads(to_events(rows))
    assert labels[read["event_id"]].observed_class == UNKNOWN   # vOLD != edit pre-version vNEW


# GIT BLOB canonical path: a git ref path is REPOSITORY-TREE-relative, so `git show HEAD:src/a.py`
# from cwd=/repo/sub must join a native edit of /repo/src/a.py -- NOT /repo/sub/src/a.py.
def test_git_blob_canonical_path_joins_native_edit_from_subdir():
    versions = {"/repo/src/a.py": "vOLD"}

    def h(p):
        return ("ok", versions[p]) if p in versions else ("absent", "absent:v1")

    j = HookJournal(":memory:")
    # resolver returns the CANONICAL worktree path (repo root + repo-relative ref path), not cwd-joined
    c = HookCapture(j, hasher=h,
                    git_blob_hasher=lambda cwd, ref, path: ("ok", "vOLD", "/repo/src/a.py"))
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Bash", {"command": "git show HEAD:src/a.py"}, cwd="/repo/sub")
    _post(c, "s", "t1", "Bash")
    _pre(c, "s", "t2", "Edit", {"file_path": "/repo/src/a.py"}, cwd="/repo")
    versions["/repo/src/a.py"] = "vNEW"
    _post(c, "s", "t2", "Edit")
    rows = j.tool_events("s:main:0")
    read = [r for r in rows if r["kind"] == "read"][0]
    edit = [r for r in rows if r["kind"] == "edit"][0]
    # cwd-joining would have produced /repo/sub/src/a.py and the two would NEVER meet
    assert read["path_normalized"] == edit["path_normalized"] == "/repo/src/a.py"
    labels = classify_reads(to_events(rows))
    assert labels[read["event_id"]].observed_class == EDIT_PRECONDITION   # vOLD == edit pre-version


def test_measure_model_visible_response_handles_arrays_and_multimodal():
    from contextruntime.hookjournal import measure_model_visible_response as m
    assert m("hello")["status"] == "text" and m("hello")["chars"] == 5
    assert m([{"type": "text", "text": "abcd"}, {"type": "text", "text": "ef"}])["chars"] == 6
    assert m([{"type": "text", "text": "hi"}, {"type": "image", "source": "x"}])["status"] == "text_partial_multimodal"
    assert m([{"type": "image", "source": "x"}])["status"] == "multimodal" and m([{"type": "image"}])["chars"] is None
    assert m({"result": "obj"})["status"] == "unsupported"


def test_unavailable_hash_is_not_stable():
    j = HookJournal(":memory:")
    c = HookCapture(j, hasher=lambda p: ("unavailable", None))      # never comparable
    _pre(c, "s", "t1", "Read", {"file_path": "/x"}); _post(c, "s", "t1", "Read")
    row = j.tool_events()[0]
    assert row["version_status"] != "stable" and row["content_version"] is None


# hook_schema 0.4.0: a grep is captured as a SEARCH materialization read with model-visible tokens,
# so agent navigation context is no longer invisible.
def test_grep_captured_as_search_read():
    j, c = _cap({})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Bash", {"command": "grep -rn needle django/utils/"}, cwd="/repo")
    _post(c, "s", "t1", "Bash")
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_name": "Bash",
                           "tool_response": "django/utils/x.py:12: needle here\n" * 20}])
    rows = j.tool_events()
    assert len(rows) == 1 and rows[0]["kind"] == "read"
    assert rows[0]["representation"] == "search" and rows[0]["channel"] == "bash_materialization"
    assert rows[0]["model_visible_tokens"] and rows[0]["token_attribution"] == "attributed"


# 0.4.0 three-way Bash ledger: execution (tests/python) is recognized but is NOT a read and NOT
# counted as unknown -- so bash_unknown_share reflects missed SOURCE context, not test-running.
def test_execution_bash_counted_separately_from_unknown():
    j, c = _cap({})
    _pre(c, "s", "t1", "Bash", {"command": "python tests/runtests.py auth"}); _post(c, "s", "t1", "Bash")
    _pre(c, "s", "t2", "Bash", {"command": "grep -rn x src"}); _post(c, "s", "t2", "Bash")
    _pre(c, "s", "t3", "Bash", {"command": "weird | thing"}); _post(c, "s", "t3", "Bash")
    rows = j.tool_events()
    assert [r["representation"] for r in rows] == ["search"]      # only grep produced a read event
    st = j.capture_stats()
    assert st["bash_calls_seen"] == 3
    assert st.get("execution_bash_calls") == 1                    # runtests -> execution, not unknown
    assert st.get("bash_materialization_calls") == 1             # grep
    assert st.get("unknown_bash_calls") == 1                     # weird
    assert abs(st["bash_unknown_share"] - 1 / 3) < 1e-9          # execution excluded from the numerator


# 0.4.1: a composite Bash call (grep + pytest) must NOT attribute the mixed response to the read.
def test_composite_bash_response_not_attributed_to_read():
    j, c = _cap({})
    c.on_event({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    _pre(c, "s", "t1", "Bash", {"command": "grep x a.py ; pytest -q"}, cwd="/repo")
    _post(c, "s", "t1", "Bash")
    _batch(c, "s", "p1", [{"tool_use_id": "t1", "tool_name": "Bash",
                           "tool_response": "a.py:1: x\n" + "PASSED " * 200}])
    reads = [r for r in j.tool_events() if r["kind"] == "read"]
    assert len(reads) == 1 and reads[0]["representation"] == "search"
    assert reads[0]["token_attribution"] == "ambiguous_composite"     # NOT 'attributed'
    assert reads[0]["model_visible_tokens"] is None                  # the mixed response is not summed
    st = j.capture_stats()
    assert st.get("bash_fully_recognized_calls") == 1                # grep + pytest both recognized
    assert st.get("execution_bash_calls", 0) == 0                   # not execution_only (it has a read)


# 0.4.1: partial recognition is NOT hidden -- grep + an unknown reader is a partial-coverage call.
def test_partial_bash_recognition_is_recorded():
    j, c = _cap({})
    _pre(c, "s", "t1", "Bash", {"command": "grep x a.py ; mystery_reader b.py"}, cwd="/repo")
    _post(c, "s", "t1", "Bash")
    st = j.capture_stats()
    assert st.get("bash_partially_recognized_calls") == 1
    assert st.get("bash_recognized_statements") == 1 and st.get("bash_unknown_statements") == 1
    assert st.get("unknown_bash_calls", 0) == 0                     # partial != unknown_only


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
