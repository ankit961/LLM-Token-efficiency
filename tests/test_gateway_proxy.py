"""B4 gateway proxy — pure upstream-body decision + a real forward/stream integration test."""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from contextruntime.gateway import RetirementGateway
from contextruntime.gateway_proxy import prepare_upstream_body, RetirementProxyHandler


def _messages_body():
    return {"model": "claude-sonnet-4-5", "max_tokens": 8, "messages": [
        {"role": "user", "content": "fix a.py"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r1", "content": "OLD BODY"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "r2", "name": "Read", "input": {"file_path": "a.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "r2", "content": "NEW BODY"}]},
    ]}


def test_observe_forwards_original_bytes():
    raw = json.dumps(_messages_body()).encode()
    g = RetirementGateway(mode="observe", lag=5, batch_turns=10)
    out, dec = prepare_upstream_body(raw, "/v1/messages", g)
    assert out is raw and dec is not None and dec.mode == "observe"    # byte-identical passthrough


def test_off_and_nonmessages_and_malformed_passthrough():
    raw = json.dumps(_messages_body()).encode()
    assert prepare_upstream_body(raw, "/v1/messages", RetirementGateway(mode="off"))[0] is raw
    assert prepare_upstream_body(raw, "/v1/models", RetirementGateway(mode="observe"))[0] is raw
    assert prepare_upstream_body(b"not json", "/v1/messages", RetirementGateway(mode="observe"))[0] == b"not json"


def test_enforce_at_boundary_reserializes_changed_bytes():
    raw = json.dumps(_messages_body()).encode()          # 2 assistant turns; batch_turns=2 ⇒ boundary
    g = RetirementGateway(mode="enforce", lag=5, batch_turns=2)
    out, dec = prepare_upstream_body(raw, "/v1/messages", g)
    assert dec.applied == 1 and out != raw
    assert "[Context note:" in json.loads(out)["messages"][2]["content"][0]["content"]  # r1 stubbed


# --- integration: proxy actually forwards to an upstream and streams the response back -------------
class _FakeUpstream(BaseHTTPRequestHandler):
    received = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        _FakeUpstream.received["body"] = self.rfile.read(n)
        _FakeUpstream.received["auth"] = self.headers.get("x-api-key")
        payload = b'{"id":"msg_1","type":"message","content":[{"type":"text","text":"ok"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_proxy_forwards_and_observe_is_byte_transparent(tmp_path, monkeypatch):
    up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    up_port = up.server_address[1]
    monkeypatch.setenv("CR_GATEWAY_UPSTREAM", f"http://127.0.0.1:{up_port}")
    monkeypatch.setenv("CR_GATEWAY_MODE", "observe")
    log = tmp_path / "gw.jsonl"
    monkeypatch.setenv("CR_GATEWAY_LOG", str(log))

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), RetirementProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    px_port = proxy.server_address[1]
    try:
        raw = json.dumps(_messages_body()).encode()
        c = http.client.HTTPConnection("127.0.0.1", px_port, timeout=10)
        c.request("POST", "/v1/messages", body=raw, headers={"Content-Type": "application/json", "x-api-key": "sk-test"})
        resp = c.getresponse()
        got = resp.read()
        assert resp.status == 200 and b"ok" in got                       # response relayed back
        assert _FakeUpstream.received["body"] == raw                       # OBSERVE forwarded bytes UNCHANGED
        assert _FakeUpstream.received["auth"] == "sk-test"                 # client auth relayed, proxy holds none
        assert log.exists() and json.loads(log.read_text().splitlines()[0])["mode"] == "observe"
        c.close()
    finally:
        proxy.shutdown()
        up.shutdown()
