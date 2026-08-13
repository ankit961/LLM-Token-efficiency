"""Phase 2.4-B — hand-rolled MCP stdio transport for the SemanticFS read surface.

Dispatch correctness, telemetry emitted through the transport (materializing calls only),
CED parent-linkage across tool calls, and a newline-delimited stdio round-trip.
"""
import io
import json
from pathlib import Path

from contextruntime.codegraph import builder
from contextruntime.mcp import SemanticFSServer, serve_stdio
from contextruntime.store import GraphStore

REPO = Path(__file__).parent / "fixtures" / "bundle_repo"


def _server():
    s = GraphStore(":memory:")
    builder.index_path(s, str(REPO), "bundle")
    return SemanticFSServer(s, session_id="sess", default_repo="bundle",
                            clock=lambda: "2026-01-01T00:00:00Z")


def _call(server, name, arguments, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})


def _meta(result):
    # the second content block is "meta: {json}"
    txt = result["result"]["content"][1]["text"]
    return json.loads(txt[len("meta: "):])


def test_initialize_and_tools_list():
    srv = _server()
    init = srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    r = init["result"]
    assert r["protocolVersion"] and "tools" in r["capabilities"]
    assert r["serverInfo"]["name"] == "contextruntime-semanticfs"
    tl = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in tl["result"]["tools"]}
    assert names == {"read_symbol", "read_slice", "find_callers", "context_search", "context_expand"}
    srv.store.close()


def test_read_symbol_tool_emits_event():
    srv = _server()
    resp = _call(srv, "read_symbol", {"symbol": "service.process", "budget": 1000})
    result = resp["result"]
    assert result["isError"] is False
    assert "def process" in result["content"][0]["text"]        # real source materialized
    meta = _meta(resp)
    assert meta["budget"]["serialized_tokens"] <= 1000
    assert meta["expansion"]["hint"] == "next"
    # the transport persisted a SemanticFS materialization event
    row = srv.store.semantic_read(meta["event_id"])
    assert row is not None and row["channel"] == "semanticfs"
    assert row["session_id"] == "sess" and row["symbol_id"].endswith("service.process")
    srv.store.close()


def test_expand_links_ced_through_transport():
    srv = _server()
    read = _call(srv, "read_symbol", {"symbol": "service.process", "budget": 1000}, mid=1)
    parent = _meta(read)["event_id"]
    sid = srv.store.semantic_read(parent)["symbol_id"]
    exp = _call(srv, "context_expand",
                {"handle": f"ctx://symbol/{sid}@implementation",
                 "parent_event_id": parent, "from_level": "identity"}, mid=2)
    assert exp["result"]["isError"] is False
    child = _meta_expand(exp)
    row = srv.store.semantic_read(child)
    assert row["channel"] == "expansion" and row["parent_event_id"] == parent
    # CED for the read = tokens of the expansion it caused
    assert srv.store.context_expansion_debt(parent) == row["serialized_tokens"] > 0
    srv.store.close()


def _meta_expand(result):
    txt = result["result"]["content"][1]["text"]
    return json.loads(txt[len("meta: "):])["event_id"]


def test_search_returns_handles_and_no_telemetry():
    srv = _server()
    resp = _call(srv, "context_search", {"query": "process"})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["results"] and all(h["handle"].startswith("ctx://symbol/") for h in body["results"])
    assert srv.store.semantic_reads() == []                     # search is not a materialization
    srv.store.close()


def test_bad_symbol_is_tool_error_not_crash():
    srv = _server()
    resp = _call(srv, "read_symbol", {"symbol": "does.not.exist"})
    assert resp["result"]["isError"] is True
    assert srv.store.semantic_reads() == []                     # nothing materialized, nothing logged
    srv.store.close()


def test_notification_gets_no_response_and_unknown_method_errors():
    srv = _server()
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    err = srv.handle({"jsonrpc": "2.0", "id": 9, "method": "bogus/method"})
    assert err["error"]["code"] == -32601
    srv.store.close()


def test_telemetry_is_committed_and_durable(tmp_path):
    # emitted events must survive the writer connection closing — a SEPARATE reader must see
    # them (regression: the server must commit per materialization, else close() rolls back).
    db = str(tmp_path / "t.db")
    w = GraphStore(db)
    builder.index_path(w, str(REPO), "bundle")
    w.commit()
    srv = SemanticFSServer(w, session_id="d", default_repo="bundle",
                           clock=lambda: "2026-01-01T00:00:00Z")
    inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "read_symbol", "arguments": {"symbol": "service.process"}}}) + "\n")
    serve_stdio(srv, inp, io.StringIO())
    w.close()
    reader = GraphStore(db)                       # fresh connection, like a separate process
    assert len(reader.semantic_reads(channel="semanticfs")) == 1
    reader.close()


def test_stdio_roundtrip():
    srv = _server()
    inp = io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),   # no response
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "read_symbol", "arguments": {"symbol": "service.process"}}}),
        "",                                                                       # blank line ignored
        "{not valid json",                                                        # malformed ignored
    ]) + "\n")
    outp = io.StringIO()
    serve_stdio(srv, inp, outp)
    responses = [json.loads(x) for x in outp.getvalue().splitlines() if x.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3]            # notification + junk produced nothing
    assert responses[2]["result"]["isError"] is False
    srv.store.close()
