"""Minimal MCP stdio server exposing the scoped `discover` executor (B5.2 Stage B).

Newline-delimited JSON-RPC 2.0 over stdio (the MCP stdio transport): handles `initialize`,
`tools/list`, `tools/call`; notifications are ignored. Stdlib only. The project root is the server's
CWD (Claude Code launches MCP servers in the session's working directory).

Run:  python -m contextruntime.discover_mcp
Wire: claude -p ... --mcp-config '{"mcpServers":{"cr":{"command":"<python>","args":["-m","contextruntime.discover_mcp"],"env":{"PYTHONPATH":"<repo>"}}}}'
"""
from __future__ import annotations

import json
import os
import sys

from .discover import discover

TOOL = {
    "name": "discover",
    "description": (
        "Fast LOCAL code discovery in one call. Give an identifier/pattern (and/or a path, and/or a "
        "traceback) and it returns ONE consolidated evidence packet: the top matching files with the "
        "relevant source slices, related test files, and a list of other matching paths. Use this "
        "FIRST for any codebase exploration instead of chains of grep/ls/cat/Read — it replaces the "
        "whole search-then-read sequence. Purely mechanical and read-only."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "regex or exact code pattern to search for"},
            "query": {"type": "string", "description": "identifier/name to search for (escaped literal)"},
            "path": {"type": "string", "description": "file to preview (head) or directory to list"},
            "traceback": {"type": "string", "description": "traceback text; its path:line frames are sliced"},
            "k": {"type": "integer", "description": "max files to expand (default 3)"},
        },
    },
}


def _handle(req):
    method = req.get("method")
    if method == "initialize":
        return {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "contextruntime-discover", "version": "0.1"}}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        p = req.get("params", {})
        if p.get("name") != "discover":
            raise ValueError(f"unknown tool {p.get('name')!r}")
        a = p.get("arguments") or {}
        res = discover(os.getcwd(), pattern=a.get("pattern"), query=a.get("query"),
                       path=a.get("path"), traceback=a.get("traceback"),
                       k=int(a.get("k") or 3))
        return {"content": [{"type": "text", "text": res["packet"]}]}
    raise ValueError(f"unknown method {method!r}")


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        if "id" not in req:                              # notification — no response
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": req["id"], "result": _handle(req)}
        except Exception as e:      # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": req["id"],
                    "error": {"code": -32603, "message": str(e)[:200]}}
        stdout.write(json.dumps(resp) + "\n")
        stdout.flush()


if __name__ == "__main__":
    serve()
