"""Phase 2.4-C -- the frozen HookJournal contract REPLAYED against REAL Claude Code hook payloads.

The fixtures in tests/fixtures/ were captured from a live `claude -p` run (Claude Code 2.1.229) with
a raw stdin tap, then sanitized (absolute paths -> /repo, session id -> sess-real) with structure and
every tool_response shape preserved byte-for-byte. This is the discipline the project learned the hard
way: unit tests on synthetic shapes only validate an INVENTED interface. These lock the contract to
the real one, and would have caught the two shape questions the docs left open:

  Q1 -- PostToolBatch tool_response is a STRING (not a {"type":"text",...} dict), so the frozen
        measure_model_visible_response(str) handles it.
  Q2 -- that string IS the model-visible text: a Read is line-number-prefixed ("1\\t..."), a Bash is
        raw stdout. So model_visible_tokens really is model-visible, and per-channel token deltas
        (line-number overhead) are real. The STRUCTURED PostToolUse tool_response (a dict) is
        correctly NOT our token source.
"""
import json
import os

from contextruntime.classify import EDIT_PRECONDITION, VERIFICATION, classify_reads
from contextruntime.hookjournal import HookCapture, HookJournal, measure_model_visible_response
from contextruntime.normalize import to_events

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name)) as fh:
        return json.load(fh)


def _batches(events, tool):
    return [b for b in events if b["hook_event_name"] == "PostToolBatch"
            and b["tool_calls"] and b["tool_calls"][0]["tool_name"] == tool]


# Q1 + Q2, straight off the real bytes: the batch Read response is a line-numbered STRING.
def test_real_postoolbatch_read_response_is_line_numbered_string():
    events = _load("real_payloads_read_bash.json")
    tr = _batches(events, "Read")[0]["tool_calls"][0]["tool_response"]
    assert isinstance(tr, str)                          # NOT a dict -> Q1: measure(str) works
    assert tr.startswith("1\t")                         # Q2: line-number prefixed == model-visible
    m = measure_model_visible_response(tr)
    assert m["status"] == "text" and m["tokens"] > 0


# The Bash batch response is raw stdout (no line numbers) -- also a plain string.
def test_real_postoolbatch_bash_response_is_raw_string():
    events = _load("real_payloads_read_bash.json")
    tr = _batches(events, "Bash")[0]["tool_calls"][0]["tool_response"]
    assert isinstance(tr, str) and not tr.startswith("1\t")
    assert measure_model_visible_response(tr)["status"] == "text"


# The STRUCTURED PostToolUse tool_response (Read=dict with file.content, Bash=dict with stdout) is a
# dict, and is DELIBERATELY not our token source -- measuring it yields unsupported, by design.
def test_real_posttooluse_response_is_structured_dict_not_the_token_source():
    events = _load("real_payloads_read_bash.json")
    for tool in ("Read", "Bash"):
        post = next(e for e in events if e["hook_event_name"] == "PostToolUse" and e["tool_name"] == tool)
        assert isinstance(post["tool_response"], dict)
        assert measure_model_visible_response(post["tool_response"])["status"] == "unsupported"


# End-to-end replay: the SAME file read two ways attributes DIFFERENT model-visible token counts
# (native line-numbered > bash raw), both to the same normalized path. This is the core signal.
def test_real_read_bash_replay_attributes_per_channel_tokens():
    events = _load("real_payloads_read_bash.json")
    j = HookJournal(":memory:")
    cap = HookCapture(j, hasher=lambda p: ("ok", "v1"))     # same bytes on both channels -> same version
    for ev in events:
        cap.on_event(ev)
    reads = [r for r in j.tool_events() if r["kind"] == "read"]
    assert {r["channel"] for r in reads} == {"native_read", "bash_materialization"}
    native = next(r for r in reads if r["channel"] == "native_read")
    bashr = next(r for r in reads if r["channel"] == "bash_materialization")
    assert native["model_visible_tokens"] > bashr["model_visible_tokens"] > 0   # line-number overhead
    assert native["token_attribution"] == bashr["token_attribution"] == "attributed"
    assert native["path_normalized"] == bashr["path_normalized"]                # joined by normalization
    st = j.capture_stats()
    assert st["errors"] == 0 and st["pre_capture_rate"] == 1.0 and st["bash_unknown_share"] == 0.0


# End-to-end replay of the MUTATION pipeline on real payloads: read -> edit -> re-read yields
# EDIT_PRECONDITION and VERIFICATION, with the edit a verified_change.
def test_real_edit_replay_labels_precondition_then_verification():
    events = _load("real_payloads_edit.json")
    j = HookJournal(":memory:")
    ver = {"v": "v1"}

    def h(p):
        return ("ok", ver["v"])

    cap = HookCapture(j, hasher=h)
    for ev in events:
        if ev.get("hook_event_name") == "PostToolUse" and ev.get("tool_name") == "Edit":
            ver["v"] = "v2"                                # the edit changes the bytes (pre v1 != post v2)
        cap.on_event(ev)
    rows = j.tool_events()
    edit = next(r for r in rows if r["kind"] == "edit")
    assert edit["mutation_status"] == "verified_change"
    labels = classify_reads(to_events(rows))
    reads = [r for r in rows if r["kind"] == "read"]
    first, second = sorted(reads, key=lambda r: r["seq"])[0], sorted(reads, key=lambda r: r["seq"])[-1]
    assert labels[first["event_id"]].observed_class == EDIT_PRECONDITION
    assert labels[second["event_id"]].observed_class == VERIFICATION


# PRIVACY regression, pinned to a real payload: the Edit's PostToolUse carries oldString/newString
# (the actual edit content), but cr-hook must persist METADATA only -- that content never lands.
def test_real_edit_content_is_never_persisted():
    events = _load("real_payloads_edit.json")
    # the fixture really does contain the pre-edit code in the Edit payload...
    edit_post = next(e for e in events if e["hook_event_name"] == "PostToolUse" and e["tool_name"] == "Edit")
    assert "hello" in json.dumps(edit_post["tool_response"])          # oldString has the old code
    j = HookJournal(":memory:")
    cap = HookCapture(j, hasher=lambda p: ("ok", "v1"))
    for ev in events:
        cap.on_event(ev)
    dump = json.dumps([dict(r) for r in j.tool_events()])
    assert "hello" not in dump and "oldString" not in dump and "newString" not in dump
