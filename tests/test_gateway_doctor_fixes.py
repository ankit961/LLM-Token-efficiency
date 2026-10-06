"""Regression tests for the 2026-10-06 gateway / doctor fixes (post-audit).

1. break-even was unreachable on Anthropic prices (the suffix it was compared with contains the
   removed tokens) — now reachable, and never fires at a loss against the cache simulator;
2. persistent retirements were not forwarded unless thinking was also stripped;
3. a mutation the API rejected was re-sent (and fell back) on every later request;
4. the thinking frontier was process-global, so a shorter conversation through the same proxy
   could lose the thinking of its latest assistant message;
5. `doctor --prefix --no-capture` crashed without a reference capture;
6. the doctor's same-project filter missed Claude Code's slug when the path contains '_';
7. the shipped reducer defaults drifted from B1_DECISION without a test pinning them.
"""
import http.client
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from contextruntime.cachealign import CacheAlignedScheduler
from contextruntime.cachemodel import PrefixCacheSim
from contextruntime.gateway import RetirementGateway, conversation_key
from contextruntime.gateway_proxy import prepare_upstream_body
from contextruntime.providers import PROFILES


def _read_then_edit(edit=True, big_chars=40_000):
    """A large Read of big.py, optionally superseded by a small Edit of the same file."""
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "task"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                           "input": {"file_path": "/w/big.py"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "X" * big_chars}]},
    ]
    if edit:
        msgs += [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Edit",
                                               "input": {"file_path": "/w/big.py",
                                                         "old_string": "a", "new_string": "b"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2",
                                          "content": "The file has been updated."}]},
        ]
    return msgs


def _thinking_conversation(first_text, n_turns):
    msgs = [{"role": "user", "content": [{"type": "text", "text": first_text}]}]
    for i in range(n_turns):
        msgs.append({"role": "assistant", "content": [
            {"type": "thinking", "thinking": "...", "signature": "sig"},
            {"type": "text", "text": f"a{i}"}]})
        msgs.append({"role": "user", "content": [{"type": "text", "text": f"u{i}"}]})
    return msgs


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch):
    for k in ("CR_GATEWAY_PROFILE", "CR_GATEWAY_THINKING_KEEP", "CR_GATEWAY_CACHE_ALIGN"):
        monkeypatch.delenv(k, raising=False)


# --- 1. break-even: reachable, and sound against the cache simulator ------------------------------
def test_break_even_reachable_when_removed_tokens_dominate_the_suffix():
    s = CacheAlignedScheduler.from_profile("gated", PROFILES["anthropic-1h"])
    s.decide([], 0, now_ts=0.0)                                   # consume the cold start
    # a 10k-token stale read followed by 2k tokens of later conversation: S = 12k contains R
    d = s.decide([("a", 1, 10_000)], 12_000, removable_tokens=9_970, now_ts=1.0)
    assert d.fire and d.reason == "break-even" and d.removable_tokens == 9_970
    assert 0.1 * 10_000 * 8 < 1.9 * 12_000          # the pre-fix rule (R·E vs the whole S) held here
    d = s.decide([("b", 2, 1_000)], 12_000, removable_tokens=970, now_ts=2.0)
    assert not d.fire and d.reason == "hold"        # a small share of the suffix still holds


def _session_bite(U, P, A, D, stub, E, mutate, r, w):
    """Cached history U + P (the pending output) + A (later content); this request appends D, then
    E later calls each append D. Total read/write-priced cost from this request on, via the same
    PrefixCacheSim the B7 model uses."""
    sim = PrefixCacheSim(ttl_s=3600.0, write_mult=w)
    sim.request(0.0, U + P + A)                                   # warm cache (same in both arms)
    if mutate:
        cur = U + stub + A + D
        rd, cr = sim.request(1.0, cur, unchanged_prefix_tokens=U)
    else:
        cur = U + P + A + D
        rd, cr = sim.request(1.0, cur)
    total = r * rd + w * cr
    for k in range(E):
        cur += D
        rd, cr = sim.request(2.0 + k, cur)
        total += r * rd + w * cr
    return total


@pytest.mark.parametrize("profile", ["anthropic-1h", "anthropic-5m", "openai-auto"])
def test_break_even_never_fires_at_a_loss(profile):
    pr = PROFILES[profile]
    r, w, E, stub = pr.read_mult, pr.write_mult, 8, 30
    fired = 0
    for U in (2_000, 30_000):
        for P in (500, 4_000, 20_000):
            for A in (0, 1_000, 8_000, 40_000):
                for D in (200, 2_000):
                    s = CacheAlignedScheduler.from_profile("gated", pr)
                    s.decide([], 0, now_ts=0.0)
                    d = s.decide([("x", 1, P)], P + A + D, removable_tokens=P - stub, now_ts=1.0)
                    if d.fire:
                        fired += 1
                        assert (_session_bite(U, P, A, D, stub, E, True, r, w)
                                <= _session_bite(U, P, A, D, stub, E, False, r, w) + 1e-6)
    assert fired > 0                                              # and the branch is reachable


def test_gateway_fires_break_even_on_anthropic_1h():
    gw = RetirementGateway(mode="enforce", align="gated", thinking_keep=0)
    _, d0 = gw.process({"model": "m", "messages": _read_then_edit(edit=False)})
    assert d0.fire_reason == "cold-start" and d0.applied == 0     # nothing retirable yet
    body = {"model": "m", "messages": _read_then_edit(edit=True)}  # the Edit supersedes the Read
    _, d1 = gw.process(body)
    assert d1.fire_reason == "break-even" and d1.fired and d1.applied == 1
    assert d1.removable_tokens > 9_000 and d1.suffix_tokens_est > d1.removable_tokens
    assert "retired" in body["messages"][2]["content"][0]["content"]


# --- 2. persistent retirements are forwarded even with thinking-GC off ----------------------------
def test_proxy_forwards_persistent_retirements_without_thinking_gc():
    gw = RetirementGateway(mode="enforce", align="gated", thinking_keep=0)
    raw1 = json.dumps({"model": "m", "messages": _read_then_edit()}).encode()
    _, d1 = prepare_upstream_body(raw1, "/v1/messages", gw)
    assert d1.fired and d1.applied == 1                           # cold start fires the stale read
    raw2 = json.dumps({"model": "m", "messages": _read_then_edit()}).encode()   # client resends originals
    out2, d2 = prepare_upstream_body(raw2, "/v1/messages", gw)
    assert d2.persistent_applied == 1 and d2.applied == 0 and d2.thinking_stripped == 0
    assert out2 is not raw2
    assert "retired" in json.loads(out2)["messages"][2]["content"][0]["content"]


# --- 3. a rejected mutation trips the conversation's breaker --------------------------------------
def test_rejected_mutation_trips_the_conversation_breaker():
    gw = RetirementGateway(mode="enforce", align="gated", thinking_keep=0)
    raw = json.dumps({"model": "m", "messages": _read_then_edit()}).encode()
    out, dec = prepare_upstream_body(raw, "/v1/messages", gw)
    assert out is not raw and dec.mutated
    gw.reject(dec)                                                # upstream answered 4xx
    out2, dec2 = prepare_upstream_body(raw, "/v1/messages", gw)
    assert out2 is raw and dec2.fire_reason == "breaker" and not dec2.mutated
    other = json.dumps({"model": "m", "messages": [{"role": "user", "content": "unrelated"}]}).encode()
    _, dec3 = prepare_upstream_body(other, "/v1/messages", gw)
    assert dec3.fire_reason != "breaker"                          # other conversations unaffected


class _RejectStubs(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n)
        _RejectStubs.bodies.append(body)
        bad = b"[Context note:" in body
        payload = (b'{"type":"error","error":{"type":"invalid_request_error","message":"no"}}' if bad
                   else b'{"id":"m","type":"message","content":[],"usage":{"input_tokens":1,"output_tokens":1}}')
        self.send_response(400 if bad else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_proxy_stops_resending_a_rejected_mutation(tmp_path, monkeypatch):
    import contextruntime.gateway_proxy as gp
    monkeypatch.setattr(gp, "_REGISTRY", None)
    _RejectStubs.bodies = []
    up = ThreadingHTTPServer(("127.0.0.1", 0), _RejectStubs)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("CR_GATEWAY_UPSTREAM", f"http://127.0.0.1:{up.server_address[1]}")
    monkeypatch.setenv("CR_GATEWAY_MODE", "enforce")
    monkeypatch.setenv("CR_GATEWAY_CACHE_ALIGN", "gated")
    log = tmp_path / "gw.jsonl"
    monkeypatch.setenv("CR_GATEWAY_LOG", str(log))
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), gp.RetirementProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        raw = json.dumps({"model": "m", "max_tokens": 8, "messages": _read_then_edit()}).encode()
        statuses = []
        for _ in range(2):
            c = http.client.HTTPConnection("127.0.0.1", proxy.server_address[1], timeout=10)
            c.request("POST", "/v1/messages", body=raw, headers={"Content-Type": "application/json"})
            resp = c.getresponse()
            resp.read()
            statuses.append(resp.status)
            c.close()
        assert statuses == [200, 200]                             # the client never sees the 400
        # request 1: mutated (400) then the original; request 2: the original only
        assert [b"[Context note:" in b for b in _RejectStubs.bodies] == [True, False, False]
        assert any(json.loads(l).get("breaker_tripped") for l in open(log))
    finally:
        proxy.shutdown()
        up.shutdown()


# --- 4. per-conversation state; the latest thinking is never stripped -----------------------------
def test_conversation_key_ignores_cache_breakpoints():
    a = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]}
    b = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
                                    {"role": "assistant", "content": "x"}]}
    c = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "other"}]}]}
    assert conversation_key(a) == conversation_key(b) != conversation_key(c)


def test_conversations_keep_separate_state_and_latest_thinking():
    gw = RetirementGateway(mode="enforce", align="gated", thinking_keep=1)
    long_body = {"model": "m", "messages": _thinking_conversation("long task", 8)}
    _, da = gw.process(long_body)
    assert da.fire_reason == "cold-start" and gw.schedulers.get(da.conv_key).strip_frontier == 7
    short_body = {"model": "m", "messages": _thinking_conversation("side call", 2)}
    _, db = gw.process(short_body)
    assert db.conv_key != da.conv_key and db.fire_reason == "cold-start"   # its own clock
    asst = [m for m in short_body["messages"] if m["role"] == "assistant"]
    assert asst[-1]["content"][0]["type"] == "thinking"           # latest thinking intact
    assert asst[0]["content"][0]["type"] == "text"                # older one stripped


def test_frontier_never_reaches_the_kept_assistant_messages():
    gw = RetirementGateway(mode="enforce", align="gated", thinking_keep=1)
    _, d = gw.process({"model": "m", "messages": _thinking_conversation("task", 3)})
    gw.schedulers.get(d.conv_key).strip_frontier = 99             # stale state from a longer past
    body = {"model": "m", "messages": _thinking_conversation("task", 3)}
    gw.process(body)
    asst = [m for m in body["messages"] if m["role"] == "assistant"]
    assert asst[-1]["content"][0]["type"] == "thinking"
    assert [m["content"][0]["type"] for m in asst[:-1]] == ["text", "text"]


# --- 5./6. doctor -----------------------------------------------------------------------------------
def test_project_files_match_both_slug_spellings(tmp_path):
    from contextruntime.prefixdoctor import project_files
    for d in ("-work-first-last-repo", "-work-first_last-repo", "-work-first-last-repo-old",
              "-work-other-repo"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "s.jsonl").write_text("{}\n")
    got = sorted(os.path.basename(os.path.dirname(f))
                 for f in project_files(str(tmp_path), "/work/first_last/repo"))
    assert got == ["-work-first-last-repo", "-work-first_last-repo"]


def test_doctor_without_capture_or_reference_runs(tmp_path):
    from contextruntime import prefixdoctor
    rep = prefixdoctor.run(str(tmp_path), capture=False, projects_dir=str(tmp_path))
    assert "no reference capture" in rep["attribution"]
    assert isinstance(prefixdoctor.format_report(rep), str)


# --- 7. shipped reducer defaults are pinned ---------------------------------------------------------
def test_shipped_reducer_defaults_are_pinned(tmp_path):
    from contextruntime.install import default_reducer_cmd, resolve_scope
    from contextruntime.reducers import hook
    from contextruntime.reducers.library import SEARCH_BUDGET_TOKENS
    assert hook.MIN_REDUCE_TOKENS == 400 and SEARCH_BUDGET_TOKENS == 256
    assert "CR_GRAPH_MODE=off" in default_reducer_cmd(resolve_scope("claude", str(tmp_path), False))
