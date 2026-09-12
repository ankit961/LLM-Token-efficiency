"""B9 local-SLM harness — retirement on OpenAI format + the agent loop against a fake upstream.
No real model calls; a scripted OpenAI-compatible server drives the loop."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from corpus.local_agent_ab import retire, run_task


def test_retire_stubs_superseded_tool_results():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "FIRST BIG READ"},
        {"role": "assistant", "tool_calls": [
            {"id": "c2", "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "SECOND READ (current)"},
        {"role": "assistant", "tool_calls": [
            {"id": "c3", "function": {"name": "read_file", "arguments": json.dumps({"path": "b.py"})}}]},
        {"role": "tool", "tool_call_id": "c3", "content": "OTHER FILE"},
    ]
    out, n = retire(msgs)
    assert n == 1                                            # only the first a.py read is superseded
    assert out[2]["content"].startswith("[retired")         # c1 stubbed
    assert out[4]["content"] == "SECOND READ (current)"      # c2 kept (latest a.py)
    assert out[6]["content"] == "OTHER FILE"                 # c3 kept (different key)
    again, n2 = retire(out)                                  # idempotent: no double-stub
    assert n2 == 0


class _FakeOpenAI(BaseHTTPRequestHandler):
    script = []          # list of assistant messages to return in order
    seen_prompt_tokens = []
    i = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        # record how many messages the harness sent (proxy for residency pressure)
        _FakeOpenAI.seen_prompt_tokens.append(len(json.dumps(req.get("messages", []))))
        msg = _FakeOpenAI.script[min(_FakeOpenAI.i, len(_FakeOpenAI.script) - 1)]
        _FakeOpenAI.i += 1
        payload = json.dumps({"choices": [{"message": msg}],
                              "usage": {"prompt_tokens": 100 + _FakeOpenAI.i, "completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_agent_loop_executes_tools_and_stops(tmp_path):
    (tmp_path / "hello.txt").write_text("old\n")
    _FakeOpenAI.script = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "write_file",
                          "arguments": json.dumps({"path": "hello.txt", "content": "NEW"})}}]},
        {"role": "assistant", "content": "DONE"},
    ]
    _FakeOpenAI.i = 0
    _FakeOpenAI.seen_prompt_tokens = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
        r = run_task(endpoint, "fake", "make hello.txt say NEW then DONE", str(tmp_path),
                     arm="N", max_steps=6)
    finally:
        srv.shutdown()
    assert r["status"] == "done" and r["calls"] == 2
    assert (tmp_path / "hello.txt").read_text() == "NEW"     # tool actually executed
    assert r["sum_prompt"] > 0                               # residency accounted
