"""Thinking-GC: strip prior-turn thinking blocks (never the latest assistant message), usage capture,
and the proxy's fall-back-to-original on upstream 4xx."""
import copy
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from contextruntime.gateway import RetirementGateway, thinking_gc, thinking_opportunity
from contextruntime.gateway_proxy import RetirementProxyHandler, extract_usage, prepare_upstream_body


def _think(sig="S" * 50):
    return {"type": "thinking", "thinking": "", "signature": sig}


def _msgs():
    """3 assistant messages, each starting with a thinking block; the last one is a tool-use turn."""
    return [
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": [_think("A" * 40), {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "body"}]},
        {"role": "assistant", "content": [{"type": "redacted_thinking", "data": "R" * 30}, {"type": "text", "text": "hmm"}]},
        {"role": "user", "content": "go on"},
        {"role": "assistant", "content": [_think("C" * 60), {"type": "tool_use", "id": "t2", "name": "Edit", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]},
    ]


def test_keep_last_1_strips_all_but_latest_assistant_message():
    m = _msgs()
    assert thinking_opportunity(m, 1) == (2, 70)                      # A(40) + R(30); latest (C) excluded
    n, sig = thinking_gc(m, 1)
    assert (n, sig) == (2, 70)
    assert [b["type"] for b in m[1]["content"]] == ["tool_use"]         # stripped
    assert [b["type"] for b in m[3]["content"]] == ["text"]             # redacted stripped too
    assert [b["type"] for b in m[5]["content"]] == ["thinking", "tool_use"]   # latest intact


def test_keep_last_2_and_keep_all_are_respected():
    m = _msgs()
    assert thinking_gc(m, 2) == (1, 40)                               # only the oldest
    assert [b["type"] for b in m[3]["content"]][0] == "redacted_thinking"
    m2 = _msgs()
    assert thinking_gc(m2, 3) == (0, 0) and m2 == _msgs()             # nothing stripped


def test_never_empties_a_message():
    m = [{"role": "assistant", "content": [_think()]},                 # thinking-only (pathological)
         {"role": "user", "content": "x"},
         {"role": "assistant", "content": [{"type": "text", "text": "done"}]}]
    assert thinking_gc(m, 1) == (0, 0)
    assert m[0]["content"][0]["type"] == "thinking"                    # left alone rather than emptied


def test_gateway_observe_counts_and_enforce_strips():
    body = {"messages": _msgs()}
    before = copy.deepcopy(body)
    g = RetirementGateway(mode="observe", thinking_keep=1)
    _, dec = g.process(body)
    assert dec.thinking_strippable == 2 and dec.thinking_stripped == 0 and body == before   # observe never mutates
    g2 = RetirementGateway(mode="enforce", thinking_keep=1, batch_turns=1000)
    _, dec2 = g2.process(body)
    assert dec2.thinking_stripped == 2
    assert [b["type"] for b in body["messages"][1]["content"]] == ["tool_use"]
    g3 = RetirementGateway(mode="enforce", thinking_keep=None)         # GC off ⇒ untouched
    b3 = {"messages": _msgs()}
    _, dec3 = g3.process(b3)
    assert dec3.thinking_strippable == 0 and b3 == {"messages": _msgs()}


def test_extract_usage_sse_and_json():
    sse = ('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":3,"cache_read_input_tokens":500,"cache_creation_input_tokens":20}}}\n\n'
           'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n').encode()
    assert extract_usage(sse) == {"input_tokens": 3, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 20, "output_tokens": 42}
    js = json.dumps({"id": "m", "usage": {"input_tokens": 1, "output_tokens": 2}}).encode()
    assert extract_usage(js) == {"input_tokens": 1, "output_tokens": 2}
    assert extract_usage(b"garbage") == {}


class _Upstream(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n))
        _Upstream.calls.append(body)
        first_asst = next(m for m in body["messages"] if m["role"] == "assistant")
        stripped = first_asst["content"][0].get("type") != "thinking"   # mutated ⇒ oldest thinking gone
        if stripped and len(_Upstream.calls) == 1:                     # reject the FIRST (mutated) attempt
            payload = b'{"type":"error","error":{"type":"invalid_request_error","message":"thinking blocks cannot be modified"}}'
            self.send_response(400)
        else:
            payload = b'{"id":"msg","usage":{"input_tokens":5,"output_tokens":1}}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_proxy_falls_back_to_original_on_upstream_400(tmp_path, monkeypatch):
    _Upstream.calls = []
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("CR_GATEWAY_UPSTREAM", f"http://127.0.0.1:{up.server_address[1]}")
    monkeypatch.setenv("CR_GATEWAY_MODE", "enforce")
    monkeypatch.setenv("CR_GATEWAY_THINKING_KEEP", "1")
    log = tmp_path / "gw.jsonl"
    monkeypatch.setenv("CR_GATEWAY_LOG", str(log))
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), RetirementProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        # only the LAST assistant message has thinking in _msgs()[5]; make an earlier one strippable
        raw = json.dumps({"messages": _msgs()}).encode()
        c = http.client.HTTPConnection("127.0.0.1", proxy.server_address[1], timeout=10)
        c.request("POST", "/v1/messages", body=raw, headers={"Content-Type": "application/json"})
        r = c.getresponse(); got = r.read(); c.close()
        assert r.status == 200 and b"msg" in got                       # client saw the successful retry
        assert len(_Upstream.calls) == 2                               # mutated attempt, then original
        assert _Upstream.calls[1] == json.loads(raw)                   # original bytes resent verbatim
        entries = [json.loads(l) for l in log.read_text().splitlines()]
        assert any(e.get("fallback_original") for e in entries)
        assert any(e.get("response_usage", {}).get("input_tokens") == 5 for e in entries)
    finally:
        proxy.shutdown()
        up.shutdown()
