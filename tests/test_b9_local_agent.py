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
    requests = []        # the raw `messages` array of each request the harness sent
    i = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        # record how many messages the harness sent (proxy for residency pressure)
        msgs = req.get("messages", [])
        if len(msgs) <= 2:          # a fresh task (system + user only) → replay the script from top
            _FakeOpenAI.i = 0
        _FakeOpenAI.seen_prompt_tokens.append(len(json.dumps(msgs)))
        _FakeOpenAI.requests.append(msgs)
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
    _FakeOpenAI.requests = []
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


def test_treatment_retires_persistently_and_does_not_overcount(tmp_path):
    """The counter-bug regression: a.py is read twice (2nd supersedes 1st), then the loop runs two
    MORE turns. `retired` must stay 1 (one distinct result retired), not grow per turn, and the
    stub must persist byte-stable in every later request. The pre-fix loop re-counted the standing
    stub each turn and would report retired == 3."""
    for f in ("a.py", "b.py", "c.py"):
        (tmp_path / f).write_text(f"# {f}\n")

    def _read(cid, path):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}]}

    _FakeOpenAI.script = [
        _read("c1", "a.py"),          # first a.py read
        _read("c2", "a.py"),          # supersedes c1
        _read("c3", "b.py"),          # keep the loop alive (distinct key)
        _read("c4", "c.py"),          # keep the loop alive (distinct key)
        {"role": "assistant", "content": "DONE"},
    ]
    _FakeOpenAI.i = 0
    _FakeOpenAI.seen_prompt_tokens = []
    _FakeOpenAI.requests = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
        r = run_task(endpoint, "fake", "read a.py twice, then more, then DONE", str(tmp_path),
                     arm="T", max_steps=8)
    finally:
        srv.shutdown()
    assert r["status"] == "done"
    assert r["retired"] == 1                                 # DISTINCT count, not a per-turn sum
    final = _FakeOpenAI.requests[-1]                         # the DONE-turn request
    stubbed = [m for m in final if m.get("role") == "tool"
               and str(m.get("content", "")).startswith("[retired")]
    assert len(stubbed) == 1                                 # c1 retired and persisted; c2/c3/c4 kept


def test_ab_end_to_end_plumbing(tmp_path):
    """ab() wiring end-to-end WITHOUT django: a real git worktree, the fake model edits a file, and
    a deliberately un-appliable test_patch drives grading to its (test_patch_applied=False) branch —
    so worktree add/remove, run_task, the grading plumbing, incremental json, and the summary all
    execute, with no python3.11 runtests needed."""
    import subprocess
    from corpus.local_agent_ab import ab

    mirror = tmp_path / "mirror"
    mirror.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(mirror), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (mirror / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(mirror), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    _FakeOpenAI.script = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "write_file",
                "arguments": json.dumps({"path": "fix.py", "content": "print('fix')\n"})}}]},
        {"role": "assistant", "content": "DONE"},
    ]
    _FakeOpenAI.i = 0
    _FakeOpenAI.seen_prompt_tokens = []
    _FakeOpenAI.requests = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {
            "endpoint": f"http://127.0.0.1:{srv.server_address[1]}", "model": "fake",
            "mirror": str(mirror), "workdir": str(tmp_path / "wd"),
            "out": str(tmp_path / "out.json"), "reps": 1, "max_steps": 6,
            "tasks": [{"instance_id": "demo-1", "base_commit": base, "problem": "add fix.py",
                       "test_patch": "(not a real patch)", "FAIL_TO_PASS": [], "PASS_TO_PASS": []}],
        }
        out = ab(cfg)
    finally:
        srv.shutdown()

    assert set(out["tasks"]["demo-1"]) == {"N0", "T0"}       # both arms, one rep each
    for k in ("N0", "T0"):
        rec = out["tasks"]["demo-1"][k]
        assert rec["metrics"]["status"] == "done"
        assert rec["grade"]["test_patch_applied"] is False   # bogus patch → grading short-circuits
        assert (tmp_path / "wd" / f"demo-1-{k}" / "fix.py").exists()   # agent edited the worktree
    assert out["summary"]["pooled_residency_delta_pct"] is not None
    assert json.load(open(tmp_path / "out.json"))["tasks"]["demo-1"]["N0"]["metrics"]["calls"] == 2


def test_recover_restores_retired_result_lossless(tmp_path):
    """With recover=True, a retired result's EXACT bytes are restorable via recover(id) — the
    provably-lossless-at-source path. The stub names the id; recover(id) pages the original back in."""
    (tmp_path / "a.py").write_text("ORIGINAL A CONTENT\n")

    def _read(cid, path):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}]}

    _FakeOpenAI.script = [
        _read("c1", "a.py"),                       # first read (will be superseded, then retired)
        _read("c2", "a.py"),                       # supersedes c1
        {"role": "assistant", "content": "", "tool_calls": [   # restore c1's exact bytes
            {"id": "r1", "type": "function",
             "function": {"name": "recover", "arguments": json.dumps({"id": "c1"})}}]},
        {"role": "assistant", "content": "DONE"},
    ]
    _FakeOpenAI.i = 0
    _FakeOpenAI.seen_prompt_tokens = []
    _FakeOpenAI.requests = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
        r = run_task(endpoint, "fake", "read a.py twice, recover the first, DONE", str(tmp_path),
                     arm="T", max_steps=8, recover=True)
    finally:
        srv.shutdown()
    assert r["status"] == "done"
    assert r["retired"] == 1 and r["recoverable"] == 1
    final = _FakeOpenAI.requests[-1]               # DONE-turn request carries the recover result
    restored = [m for m in final if m.get("tool_call_id") == "r1"]
    assert restored and restored[0]["content"] == "ORIGINAL A CONTENT\n"   # exact bytes back

    # default (recover off) never stores and never offers a recover id
    _FakeOpenAI.i = 0
    _FakeOpenAI.requests = []
    srv2 = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    try:
        ep2 = f"http://127.0.0.1:{srv2.server_address[1]}"
        r2 = run_task(ep2, "fake", "same but no recover", str(tmp_path), arm="T", max_steps=8)
    finally:
        srv2.shutdown()
    assert r2["recoverable"] == 0
