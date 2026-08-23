"""B4 — minimal OBSERVE deployment: an HTTP proxy that runs the RetirementGateway per request.

Point the client at this proxy (`ANTHROPIC_BASE_URL=http://localhost:8787`) and it relays every request
to the real API, calling `RetirementGateway.process` on the way through. In `observe` mode it forwards
the request bytes UNCHANGED and only logs the retirement opportunity; in `enforce` mode it re-serializes
the mutated body at batch boundaries. The client's own auth headers are relayed verbatim — the proxy
never holds a key.

Stdlib only (http.server + http.client). Response bodies stream through chunk-by-chunk (delimited by
connection close), so SSE / streaming responses are not buffered. Config via env:

    CR_GATEWAY_MODE      off | observe | enforce   (default off)
    CR_GATEWAY_LOG       path to the OBSERVE decision log (optional)
    CR_GATEWAY_UPSTREAM  upstream base URL          (default https://api.anthropic.com)
    CR_GATEWAY_PORT      listen port                (default 8787)

Run: `python -m contextruntime.gateway_proxy`  (or import `serve`).
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .gateway import RetirementGateway

MESSAGES_PATH = "/v1/messages"
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "host", "content-length"}


def prepare_upstream_body(raw: bytes, path: str, gateway: RetirementGateway):
    """Pure decision: what bytes go upstream, and the gateway decision. OBSERVE (and enforce-no-op)
    return the ORIGINAL bytes so the request is byte-transparent; only an enforce that actually applied
    re-serializes. Non-messages paths and malformed bodies pass through untouched (fail-open)."""
    if not path.split("?", 1)[0].endswith(MESSAGES_PATH) or gateway.mode == "off":
        return raw, None
    try:
        body = json.loads(raw)
    except Exception:      # noqa: BLE001 — not JSON we understand ⇒ pass through
        return raw, None
    body_out, dec = gateway.process(body)
    if gateway.mode == "enforce" and dec is not None and dec.applied:
        try:
            return json.dumps(body_out).encode("utf-8"), dec
        except Exception:      # noqa: BLE001
            return raw, dec
    return raw, dec


def _upstream():
    return os.environ.get("CR_GATEWAY_UPSTREAM") or "https://api.anthropic.com"


def _connect(upstream: str):
    u = urllib.parse.urlparse(upstream)
    port = u.port or (443 if u.scheme == "https" else 80)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return cls(u.hostname, port, timeout=600), u.hostname


class RetirementProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):      # keep the proxy quiet; the decision log is the record
        pass

    def _gateway(self):
        return RetirementGateway(log_path=os.environ.get("CR_GATEWAY_LOG"))

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _fwd_headers(self, host: str, body_len: int):
        h = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
        h["Host"] = host
        h["Content-Length"] = str(body_len)
        return h

    def _relay(self, method: str, body: bytes):
        conn, host = _connect(_upstream())
        try:
            conn.request(method, self.path, body=body or None, headers=self._fwd_headers(host, len(body or b"")))
            resp = conn.getresponse()
        except Exception as e:      # noqa: BLE001
            self.send_error(502, f"upstream error: {type(e).__name__}")
            conn.close()
            return
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")          # EOF-delimited body ⇒ streaming passthrough
        self.end_headers()
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:      # noqa: BLE001 — client hung up mid-stream
            pass
        finally:
            conn.close()

    def do_POST(self):
        raw = self._read_body()
        try:
            body_out, _dec = prepare_upstream_body(raw, self.path, self._gateway())
        except Exception:      # noqa: BLE001 — the gateway must never break the request path
            body_out = raw
        self._relay("POST", body_out)

    def do_GET(self):
        self._relay("GET", b"")


def serve(port: int = None, *, host: str = "127.0.0.1"):
    port = port or int(os.environ.get("CR_GATEWAY_PORT", "8787"))
    mode = RetirementGateway().mode
    httpd = ThreadingHTTPServer((host, port), RetirementProxyHandler)
    print(f"[cr-gateway] listening on http://{host}:{port}  mode={mode}  upstream={_upstream()}  "
          f"log={os.environ.get('CR_GATEWAY_LOG') or '(none)'}")
    print(f"[cr-gateway] point the client at it:  export ANTHROPIC_BASE_URL=http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    serve()
