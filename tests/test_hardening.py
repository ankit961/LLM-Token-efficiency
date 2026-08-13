"""Pre-Phase-2 hardening: idempotent re-ingest, cross-session isolation,
redaction of stored payloads, and handle resolution (context_expand)."""
from pathlib import Path

from contextruntime.redact import redact, redaction_count
from contextruntime.residency import ingest_file
from contextruntime.semanticfs import context_expand
from contextruntime.store import GraphStore

FIX = Path(__file__).parent / "fixtures" / "synthetic_session.jsonl"


def _counts(s):
    return (s.count("objects"), s.count("requests"), s.count("islands"),
            s.edge_count("RESIDENT_IN"), s.edge_count("DUPLICATE_OF"), s.edge_count("BROKE"))


def test_reingest_is_idempotent():
    s = GraphStore(":memory:")
    ingest_file(s, FIX)
    first = _counts(s)
    ingest_file(s, FIX)          # same session again
    ingest_file(s, FIX)          # and again
    assert _counts(s) == first    # no duplication of nodes or edges
    s.close()


def test_sessions_are_isolated():
    s = GraphStore(":memory:")
    ingest_file(s, FIX)
    n1 = s.count("objects")
    # ingest the same content under a different session id -> distinct nodes
    import json
    other = Path(FIX).with_name("_other.jsonl")
    other.write_text("\n".join(
        json.dumps({**json.loads(ln), "sessionId": "other"})
        for ln in FIX.read_text().splitlines() if ln.strip()))
    try:
        ingest_file(s, other)
        assert s.count("objects") == 2 * n1        # no cross-session collision
        # deleting one session leaves the other intact
        s.delete_session("synthetic")
        assert s.count("objects") == n1
    finally:
        other.unlink(missing_ok=True)
    s.close()


def test_redaction_scrubs_secrets():
    raw = ("connecting...\n"
           "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI1234567890abcdEXAMPLEKEY\n"
           "token AKIAIOSFODNN7EXAMPLE used\n"
           "Authorization: Bearer abcdef0123456789abcdef0123456789\n"
           "sk-abcdefghijklmnopqrstuvwxyz012345\n")
    out = redact(raw)
    assert "wJalrXUtnFEMI1234567890abcdEXAMPLEKEY" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "[REDACTED:" in out
    assert redaction_count(raw) >= 4
    assert "connecting..." in out                  # non-secrets survive


def test_stored_blob_is_redacted():
    import json
    s = GraphStore(":memory:")
    secret_line = "export API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345\n" * 40
    rec = [
        json.dumps({"type": "user", "sessionId": "sec", "uuid": "u1",
                    "message": {"role": "user", "content": "go"}}),
        json.dumps({"type": "assistant", "sessionId": "sec", "uuid": "a1",
                    "requestId": "r1", "message": {"model": "claude-opus-4-8",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                 "input": {"command": "cat .env"}}],
                    "usage": {"input_tokens": 5, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 100, "output_tokens": 5}}}),
        json.dumps({"type": "user", "sessionId": "sec", "uuid": "u2",
                    "message": {"role": "user", "content": [{"type": "tool_result",
                    "tool_use_id": "t1", "content": secret_line}]}}),
    ]
    p = Path(FIX).with_name("_sec.jsonl")
    p.write_text("\n".join(rec))
    try:
        ingest_file(s, p)
        blobs = "".join(r["sample"] or "" for r in
                        s.conn.execute("SELECT sample FROM blobs"))
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in blobs
        assert "[REDACTED:" in blobs
    finally:
        p.unlink(missing_ok=True)
    s.close()


def test_context_expand_resolves_and_reports_expiry():
    s = GraphStore(":memory:")
    ingest_file(s, FIX)
    row = s.conn.execute("SELECT content_hash FROM blobs LIMIT 1").fetchone()
    good = context_expand(s, f"result://{row['content_hash']}")
    assert good.found and good.text
    missing = context_expand(s, "result://deadbeefdeadbeef")
    assert not missing.found and "re-run" in missing.note        # never a silent empty
    bad = context_expand(s, "http://nope")
    assert not bad.found
    s.close()
