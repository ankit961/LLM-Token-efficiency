"""cr doctor --prefix — itemize/classify, evidence scan, recommendations, and the capture proxy."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from contextruntime import prefixdoctor as pd


def _body():
    big = {"type": "object", "properties": {k: {"type": "string", "description": "x" * 200} for k in "abcdefgh"}}
    return {"model": "m", "stream": True,
            "system": [{"type": "text", "text": "You are Claude Code. " * 20},
                       {"type": "text", "text": "# claudeMd\nContents of CLAUDE.md " + "rule " * 8000}],
            "tools": [{"name": "Bash", "description": "run", "input_schema": big},
                      {"name": "Workflow", "description": "orchestrate " * 400, "input_schema": big},
                      {"name": "mcp__gmail__send", "description": "send mail " * 300, "input_schema": big},
                      {"name": "mcp__gmail__list", "description": "list mail " * 300, "input_schema": big}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "<system-reminder>memory MEMORY.md</system-reminder>"},
                                                      {"type": "text", "text": "fix the bug"}]}]}


def test_itemize_groups_tools_by_server_and_classifies_blocks():
    items = pd.itemize(_body())
    groups = {it.group for it in items}
    assert {"tool:builtin", "tool:gmail", "system", "injected", "user"} <= groups
    names = {it.name for it in items}
    assert "system[1]:claude_md" in names and "msg0[0]:memory" in names
    s = pd.summarize_items(items)
    assert s["n_tools"] == 4 and s["tools_est_tokens"] > 0
    assert s["by_group"]["tool:gmail"] > 0


def test_report_recommends_unused_heavy_server_and_builtin_with_flags():
    ev = pd.Evidence(sessions=10, startup_prefix=[40000] * 10, calls_per_session=[50] * 10,
                     per_session_P=[40000 * 50 * 1.3] * 10)
    ev.builtin_tool_uses.update({"Bash": 500})                      # Workflow never used; gmail never used
    rep = pd.build_report(pd.itemize(_body()), ev, {}, real_first_P=None)
    acts = " | ".join(r["action"] for r in rep["recommendations"])
    assert 'mcp__gmail__*' in acts                                  # whole unused server
    assert "--disallowedTools Workflow" in acts                     # unused heavy builtin
    assert "trim system[1]:claude_md" in acts                       # large resident block
    assert rep["median_calls_per_session"] == 50
    assert 0 < rep["fixed_prefix_share_of_sum_P_median"] <= 100
    assert rep["potential_tokens_per_call_saved"] > 0


def test_calibration_anchors_to_real_first_call():
    ev = pd.Evidence()
    items = pd.itemize(_body())
    est = pd.summarize_items(items)["total_est_tokens"]
    rep = pd.build_report(items, ev, {}, real_first_P=est * 2)
    assert rep["calibration_factor"] == 2.0 and rep["startup_prefix_est_tokens"] == est * 2


def test_usage_evidence_scans_transcripts(tmp_path):
    proj = tmp_path / "-proj"
    proj.mkdir()
    u = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 30000, "input_tokens": 5, "output_tokens": 3}
    rows = [
        {"type": "assistant", "requestId": "r1", "message": {"usage": u, "content": [
            {"type": "tool_use", "id": "a", "name": "mcp__gmail__send", "input": {}},
            {"type": "tool_use", "id": "b", "name": "Skill", "input": {"skill": "pdf"}}]}},
        {"type": "assistant", "requestId": "r2", "message": {"usage": {**u, "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 100},
                                                            "content": [{"type": "tool_use", "id": "c", "name": "Bash", "input": {}}]}},
    ]
    (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    ev = pd.usage_evidence(str(tmp_path), max_sessions=5)
    assert ev.sessions == 1 and ev.calls_per_session == [2] and ev.startup_prefix == [30005]
    assert ev.mcp_server_uses["gmail"] == 1 and ev.skill_uses["pdf"] == 1 and ev.builtin_tool_uses["Bash"] == 1


def test_capture_handler_records_main_request_and_rejects():
    pd._Capture.bodies, pd._Capture.event = [], threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pd._CaptureHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        aux = json.dumps({"messages": [{"role": "user", "content": "title?"}]}).encode()
        c.request("POST", "/v1/messages", body=aux, headers={"x-api-key": "sk-secret", "Content-Type": "application/json"})
        r = c.getresponse(); r.read(); c.close()
        assert r.status == 400                                      # rejected, not forwarded
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        main = json.dumps(_body()).encode()
        c.request("POST", "/v1/messages", body=main, headers={"x-api-key": "sk-secret", "Content-Type": "application/json"})
        r = c.getresponse(); r.read(); c.close()
        assert r.status == 400 and pd._Capture.event.is_set()      # main (with tools) captured
        assert len(pd._Capture.bodies) == 2
        assert "sk-secret" not in json.dumps(pd._Capture.bodies)    # auth never stored
    finally:
        srv.shutdown()
