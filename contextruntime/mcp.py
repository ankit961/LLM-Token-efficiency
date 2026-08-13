"""Hand-rolled MCP stdio transport for the SemanticFS read surface (Phase 2.4-B).

No third-party dependency (deliberate): a minimal JSON-RPC 2.0 loop over newline-delimited
stdio, implementing just enough of MCP — `initialize` / `tools/list` / `tools/call` / `ping`
— to expose the read surface to an agent. EVERY materializing call emits a SemanticReadEvent
(`contextruntime.telemetry`), so the transport is instrumented from the start rather than
having telemetry bolted on later.

Channel mapping: `read_symbol`/`read_slice` and `context_expand` MATERIALIZE source into the
model's context, so they emit events (expansions carry `parent_event_id` for CED). `find_callers`
and `context_search` return HANDLES, not bodies — not materializations — so they don't.

Observe-only (Phase 2.4): nothing is ever denied; classification/outcome columns stay null
until the 2.4-C retrospective labeller fills them.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import __version__
from .semanticfs import (context_expand, context_search, find_callers, read_slice,
                         read_symbol)
from .store import GraphStore
from .telemetry import record_expansion, record_read

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "read_symbol",
     "description": "Materialize a symbol and its budgeted dependency neighborhood as "
                    "source-derived text within a token budget. Returns rendered source plus "
                    "an event_id, budget accounting, and progressive @next expansion handles.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "symbol": {"type": "string", "description": "symbol_id or qualified name"},
                         "budget": {"type": "integer", "default": 2048},
                         "resolution": {"type": "string",
                                        "enum": ["adaptive", "identity", "signature", "skeleton",
                                                 "slice", "implementation"], "default": "adaptive"},
                         "repo": {"type": "string"}},
                     "required": ["symbol"]}},
    {"name": "read_slice",
     "description": "Materialize just this symbol at slice level within a budget (no dependencies).",
     "inputSchema": {"type": "object",
                     "properties": {"symbol": {"type": "string"},
                                    "budget": {"type": "integer", "default": 512},
                                    "repo": {"type": "string"}},
                     "required": ["symbol"]}},
    {"name": "find_callers",
     "description": "Reverse CALLS traversal — who calls this symbol. Returns handles, not code.",
     "inputSchema": {"type": "object",
                     "properties": {"symbol": {"type": "string"},
                                    "limit": {"type": "integer", "default": 20},
                                    "repo": {"type": "string"}},
                     "required": ["symbol"]}},
    {"name": "context_search",
     "description": "Structural symbol search (exact/short-name/path). Returns ranked handles, "
                    "never code dumps — page into them with read_symbol / context_expand.",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "limit": {"type": "integer", "default": 10},
                                    "repo": {"type": "string"}},
                     "required": ["query"]}},
    {"name": "context_expand",
     "description": "Resolve a handle to its payload. A bare ctx://symbol/<id> expands to a "
                    "bounded signature; append @implementation to escalate. Pass parent_event_id "
                    "(the read this expands) so Context Expansion Debt is attributed.",
     "inputSchema": {"type": "object",
                     "properties": {"handle": {"type": "string"},
                                    "parent_event_id": {"type": "string"},
                                    "from_level": {"type": "string"}},
                     "required": ["handle"]}},
]


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


class SemanticFSServer:
    def __init__(self, store: GraphStore, session_id: str, default_repo: Optional[str] = None,
                 clock=None):
        self.store = store
        self.session_id = session_id
        self.default_repo = default_repo
        # injectable clock keeps tests deterministic; real server uses UTC wall time for ts only
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    # --- JSON-RPC routing ----------------------------------------------------

    def handle(self, msg: dict) -> Optional[dict]:
        """Route one JSON-RPC message. Returns a response dict, or None for a notification."""
        if "id" not in msg:                      # notification (e.g. notifications/initialized)
            return None
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            return self._ok(mid, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "contextruntime-semanticfs", "version": __version__}})
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                return self._ok(mid, self.call_tool(name, args, mid))
            except KeyError as e:
                return self._err(mid, -32602, f"missing argument: {e}")
            except Exception as e:  # noqa: BLE001 - report, never crash the loop
                return self._err(mid, -32603, f"tool error: {e}")
        return self._err(mid, -32601, f"method not found: {method}")

    @staticmethod
    def _ok(mid, result) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid, code, message) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(text: str) -> dict:
        return {"content": [_text(text)], "isError": True}

    # --- tool dispatch -------------------------------------------------------

    def call_tool(self, name: str, args: dict, request_id) -> dict:
        repo = args.get("repo") or self.default_repo
        rid = str(request_id)
        if name == "read_symbol":
            rr = read_symbol(self.store, args["symbol"], budget=args.get("budget", 2048),
                             resolution=args.get("resolution", "adaptive"), repo_id=repo)
            if not rr.ok:
                return self._tool_error(rr.note)
            eid = record_read(self.store, rr, session_id=self.session_id, request_id=rid,
                              repo_id=repo, ts=self._clock())
            self.store.commit()                  # durable per materialization (survives process exit)
            return self._read_payload(rr, eid)
        if name == "read_slice":
            rr = read_slice(self.store, args["symbol"], budget=args.get("budget", 512), repo_id=repo)
            if not rr.ok:
                return self._tool_error(rr.note)
            eid = record_read(self.store, rr, session_id=self.session_id, request_id=rid,
                              repo_id=repo, ts=self._clock())
            self.store.commit()
            return self._read_payload(rr, eid)
        if name == "find_callers":
            callers = find_callers(self.store, args["symbol"], limit=args.get("limit", 20), repo_id=repo)
            return {"content": [_text(json.dumps({"callers": callers}, indent=2))], "isError": False}
        if name == "context_search":
            hits = context_search(self.store, args["query"], repo_id=repo, limit=args.get("limit", 10))
            return {"content": [_text(json.dumps({"results": hits}, indent=2))], "isError": False}
        if name == "context_expand":
            exp = context_expand(self.store, args["handle"])
            eid = None
            parent = args.get("parent_event_id")
            if parent:                           # link the expansion to the read that caused it (CED)
                eid = record_expansion(self.store, exp, parent_event_id=parent,
                                       session_id=self.session_id, request_id=rid,
                                       from_level=args.get("from_level"), ts=self._clock())
                self.store.commit()
            if not exp.found:
                return self._tool_error(exp.note)
            meta = {"event_id": eid, "level": exp.level, "kind": exp.kind, "note": exp.note}
            return {"content": [_text(exp.text), _text("meta: " + json.dumps(meta))],
                    "isError": False}
        return self._tool_error(f"unknown tool: {name}")

    def _read_payload(self, rr, event_id: str) -> dict:
        b = rr.budget
        meta = {"event_id": event_id,
                "budget": {"requested": b["requested"], "serialized_tokens": b["serialized_tokens"],
                           "source_body_tokens": b["source_body_tokens"],
                           "protocol_overhead_ratio": b["protocol_overhead_ratio"],
                           "budget_insufficient": b["budget_insufficient"]},
                "expansion": {"hint": rr.expansion.get("hint"), "next": rr.expansion.get("next")},
                "graph": rr.graph}
        return {"content": [_text(rr.to_text()), _text("meta: " + json.dumps(meta))],
                "isError": False}


def serve_stdio(server: SemanticFSServer, inp=None, outp=None) -> None:
    inp = inp or sys.stdin
    outp = outp or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue                             # ignore malformed frames rather than crash
        resp = server.handle(msg)
        if resp is not None:
            outp.write(json.dumps(resp) + "\n")
            outp.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="contextruntime-mcp",
                                 description="SemanticFS read surface over MCP stdio (observe-only)")
    ap.add_argument("--db", required=True, help="sqlite store (telemetry is persisted here)")
    ap.add_argument("--repo", help="default repo_id for reads")
    ap.add_argument("--session", help="session id (default: generated)")
    args = ap.parse_args(argv)
    store = GraphStore(args.db)
    session = args.session or ("mcp-" + uuid.uuid4().hex[:12])
    serve_stdio(SemanticFSServer(store, session_id=session, default_repo=args.repo))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
