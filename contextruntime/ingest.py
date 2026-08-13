"""Ingest Claude Code transcripts into normalized session records.

JSONL is the load-bearing source (design §5): reconcile by ``requestId`` because
streaming writes several rows per request. OTel / statusline are opt-in and are
NOT consumed here.

``load_session`` returns (requests, events, segment_bounds):
  requests  — one reconciled Request per requestId, in turn order
  events    — content units (tool results, texts) with their entry turn
  seg_bounds — turn indices where a compaction segment ends (residency horizon)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import SCHEMA_VERSION
from .model import Request, content_hash

CHARS_PER_TOKEN = 4.0
IMAGE_TOKENS = 1100

# A Bash result whose command is a test runner is a test_result (routes to the
# failure-preserving reducer), not a generic log.
_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|"
                       r"npm (run )?test|rspec|phpunit|tox)\b")


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) if text else 0


@dataclass
class ContentEvent:
    entry_turn: int
    kind: str
    text: str
    provenance: str
    source_ref: Optional[str] = None
    tool_name: Optional[str] = None

    @property
    def hash(self) -> str:
        return content_hash(self.text)

    @property
    def token_est(self) -> int:
        return est_tokens(self.text)


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict) and b.get("type") in ("text", "tool_reference"):
                parts.append(str(b.get("text", "")))
    return "\n".join(parts)


def _categorize_tool(name: str) -> tuple[str, str]:
    """Return (kind, provenance) for a tool result."""
    if name == "Read":
        return "source_slice", "source"
    if name in ("Grep", "Glob"):
        return "search_result", "tool"
    if name == "Bash":
        return "log", "tool"
    if name in ("WebFetch", "WebSearch"):
        return "log", "external"
    return "tool_result", "tool"


def load_session(path: str | Path) -> tuple[list[Request], list[ContentEvent], list[int]]:
    requests: dict[str, Request] = {}      # requestId -> Request (last usage wins)
    order: list[str] = []
    events: list[ContentEvent] = []
    seg_bounds: list[int] = []
    tool_reg: dict[str, tuple[str, dict]] = {}   # tool_use_id -> (name, input)
    session_id = Path(path).stem

    def cur_turn() -> int:
        return len(order)

    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("type")

            if t == "system" and rec.get("subtype") == "compact_boundary":
                seg_bounds.append(cur_turn())
                continue

            if t == "assistant":
                msg = rec.get("message") or {}
                model = msg.get("model") or ""
                if model == "<synthetic>":
                    continue
                usage = msg.get("usage")
                rid = rec.get("requestId") or msg.get("id")
                if isinstance(usage, dict) and rid:
                    cc = usage.get("cache_creation") or {}
                    cw = usage.get("cache_creation_input_tokens", 0) or 0
                    if isinstance(cc, dict):
                        cw = (cc.get("ephemeral_1h_input_tokens", 0) or 0) + \
                             (cc.get("ephemeral_5m_input_tokens", 0) or 0) or cw
                    if rid not in requests:
                        order.append(rid)
                    requests[rid] = Request(
                        request_id=rid, session_id=rec.get("sessionId", session_id),
                        turn=requests[rid].turn if rid in requests else len(order) - 1,
                        model=model, ts=rec.get("timestamp"),
                        input_tokens=usage.get("input_tokens", 0) or 0,
                        cache_read=usage.get("cache_read_input_tokens", 0) or 0,
                        cache_creation=cw,
                        output_tokens=usage.get("output_tokens", 0) or 0,
                        measurement_quality="reconciled", schema_version=SCHEMA_VERSION,
                    )
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        tool_reg[b.get("id")] = (b.get("name", "?"), b.get("input") or {})
                    elif b.get("type") == "text":
                        txt = b.get("text", "")
                        if txt:
                            events.append(ContentEvent(cur_turn(), "assistant_msg", txt, "model"))
                continue

            if t == "user":
                msg = rec.get("message") or {}
                content = msg.get("content")
                is_meta = bool(rec.get("isMeta"))
                if isinstance(content, str):
                    events.append(ContentEvent(cur_turn(),
                                  "rule" if is_meta else "user_msg", content,
                                  "source" if is_meta else "user"))
                    continue
                for b in (content or []):
                    if isinstance(b, str):
                        events.append(ContentEvent(cur_turn(), "user_msg", b, "user"))
                    elif isinstance(b, dict) and b.get("type") == "tool_result":
                        name, binput = tool_reg.get(b.get("tool_use_id"), ("?", {}))
                        text = _text_of(b.get("content"))
                        if not text and rec.get("toolUseResult") is not None:
                            tur = rec["toolUseResult"]
                            text = tur if isinstance(tur, str) else json.dumps(tur, default=str)
                        kind, prov = _categorize_tool(name)
                        src = None
                        if name == "Read" and binput.get("file_path"):
                            src = str(binput["file_path"])
                        elif name == "Bash" and binput.get("command"):
                            cmd = str(binput["command"])
                            src = "bash:" + cmd[:80]
                            if _TEST_CMD.search(cmd):
                                kind = "test_result"
                        events.append(ContentEvent(cur_turn(), kind, text, prov,
                                      source_ref=src, tool_name=name))
                    elif isinstance(b, dict) and b.get("type") == "text":
                        events.append(ContentEvent(cur_turn(), "user_msg", b.get("text", ""), "user"))
                continue

    # finalize turn indices in insertion order
    for turn, rid in enumerate(order):
        requests[rid].turn = turn
    return [requests[r] for r in order], events, seg_bounds
