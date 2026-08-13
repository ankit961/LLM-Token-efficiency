"""SemanticReadEvent recording (design v1.2, Phase 2.4) — observe-only admission telemetry.

Kept SEPARATE from the read functions on purpose: `read_symbol`/`context_expand` stay pure,
and the TRANSPORT (the MCP server, or an instrumented CLI/hook) is where the live session
context actually exists — so it calls `record_read`/`record_expansion` after each read. This
also means the same telemetry is emitted no matter which channel served the read.

Event ids are DETERMINISTIC (content-hashed, no wall-clock), so a run — and its tests — are
reproducible; timestamps are passed in by the caller rather than read from the clock here.

Nothing in this module enforces admission. 2.4 is observe-only: every event is `allowed`, the
classification/outcome columns stay null until the 2.4-C retrospective labeller fills them.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .ingest import est_tokens
from .model import SemanticReadEvent
from .store import GraphStore


def _event_id(*parts) -> str:
    h = hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode("utf-8", "replace"))
    return "ev_" + h.hexdigest()[:16]


def record_read(store: GraphStore, rr, *, session_id: Optional[str] = None,
                request_id: Optional[str] = None, channel: str = "semanticfs",
                stream_key: Optional[str] = None, ts: Optional[str] = None,
                repo_id: Optional[str] = None, predicted_class: Optional[str] = None,
                predicted_confidence: Optional[float] = None, allowed: bool = True,
                nudged: bool = False, bypass_channel: Optional[str] = None) -> str:
    """Record a read (SemanticFS, or an attributed native/Bash materialization) from its
    ReadResult. Returns the event_id — thread it into `record_expansion` as
    `parent_event_id` so Context Expansion Debt links back to the read that caused it."""
    seq = store.next_read_seq()
    b = rr.budget or {}
    root = rr.sections[0] if rr.sections else None
    prov = root.get("provenance", {}) if root else {}
    levels = ",".join(s["level"] for s in rr.sections) if rr.sections else None
    eid = _event_id(session_id, request_id, channel, rr.root, seq)
    ev = SemanticReadEvent(
        event_id=eid, seq=seq, channel=channel, session_id=session_id,
        stream_key=stream_key or session_id, request_id=request_id, ts=ts,
        repo_id=repo_id, path=prov.get("path"), symbol_id=rr.root,
        content_hash=prov.get("content_hash"),
        predicted_class=predicted_class, predicted_confidence=predicted_confidence,
        allowed=int(bool(allowed)), denied=int(not allowed), nudged=int(bool(nudged)),
        bypass_channel=bypass_channel,
        representation=levels,
        materialization_quality=(root.get("materialization_quality") if root else None),
        serialized_tokens=b.get("serialized_tokens"),
        source_body_tokens=b.get("source_body_tokens"),
        protocol_overhead=b.get("protocol_overhead_ratio"),
        budget_requested=b.get("requested"))
    store.put_semantic_read(ev)
    return eid


def record_expansion(store: GraphStore, exp, *, parent_event_id: str,
                     session_id: Optional[str] = None, request_id: Optional[str] = None,
                     stream_key: Optional[str] = None, ts: Optional[str] = None,
                     from_level: Optional[str] = None, reason: Optional[str] = None,
                     repo_id: Optional[str] = None) -> Optional[str]:
    """Record an explicit expansion, LINKED to the read that caused it. Its serialized
    tokens are exactly the Context Expansion Debt this expansion adds to that read.
    Returns None for a not-found expansion (nothing was materialized)."""
    if not exp.found:
        return None
    seq = store.next_read_seq()
    tokens = est_tokens(exp.text)
    eid = _event_id(session_id, request_id, "expansion", exp.symbol_id, parent_event_id, seq)
    ev = SemanticReadEvent(
        event_id=eid, seq=seq, channel="expansion", session_id=session_id,
        stream_key=stream_key or session_id, request_id=request_id, ts=ts,
        repo_id=repo_id, symbol_id=exp.symbol_id,
        serialized_tokens=tokens, source_body_tokens=tokens, protocol_overhead=0.0,
        parent_event_id=parent_event_id, from_level=from_level, to_level=exp.level,
        reason=reason)
    store.put_semantic_read(ev)
    return eid
