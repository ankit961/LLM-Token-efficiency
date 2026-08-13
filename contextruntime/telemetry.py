"""SemanticReadEvent recording (design v1.2, Phase 2.4) — observe-only admission telemetry.

Kept SEPARATE from the read functions on purpose: `read_symbol`/`context_expand` stay pure,
and the TRANSPORT (the MCP server, or an instrumented CLI/hook) is where the live session
context actually exists — so it calls `record_read`/`record_expansion` after each read. This
also means the same telemetry is emitted no matter which channel served the read.

Event IDENTITY (2.4-B.1): `event_id` is a FRESH id per materialization (a UUID). It must NOT
be a hash of (session, request, channel, symbol) — two genuine materializations can share all
of those (a request that reads the same symbol twice; a parentless repeated expansion; native/
bash reads with an absent or reused request_id), and a content-hashed id would silently collapse
them into one row, under-counting the very events 2.4-C population-counts. INTENTIONAL replay
idempotence (a hook re-delivering the same producer event) is a SEPARATE concern, keyed on the
optional `source_event_key` (a tool_use_id / transcript uuid / hook id). Timestamps are still
passed in by the caller, never read from the clock here.

Nothing in this module enforces admission. 2.4 is observe-only: every event is `allowed`, the
classification/outcome columns stay null until the 2.4-C retrospective labeller fills them.
"""
from __future__ import annotations

import uuid
from typing import Optional

from .ingest import est_tokens
from .model import SemanticReadEvent
from .store import GraphStore


def _new_event_id() -> str:
    return "ev_" + uuid.uuid4().hex


def record_read(store: GraphStore, rr, *, session_id: Optional[str] = None,
                request_id: Optional[str] = None, channel: str = "semanticfs",
                stream_key: Optional[str] = None, ts: Optional[str] = None,
                repo_id: Optional[str] = None, predicted_class: Optional[str] = None,
                predicted_confidence: Optional[float] = None, allowed: bool = True,
                nudged: bool = False, bypass_channel: Optional[str] = None,
                source_event_key: Optional[str] = None) -> str:
    """Record a read (SemanticFS, or an attributed native/Bash materialization) from its
    ReadResult. Returns the event_id — thread it into `record_expansion` as
    `parent_event_id` so Context Expansion Debt links back to the read that caused it.

    Each call is a DISTINCT event (fresh UUID). Pass `source_event_key` only for intentional
    replay idempotence (hook/transcript re-delivery): a second call with the same key returns
    the already-stored event_id instead of a duplicate row. `seq` is DB-assigned (AUTOINCREMENT);
    `transport_content_tokens` defaults to the semantic payload until a transport calls
    `store.update_transport_tokens` with the real full-response size."""
    if source_event_key:
        existing = store.semantic_read_by_source_key(source_event_key)
        if existing is not None:
            return existing["event_id"]          # intentional replay: canonical id, no dup row
    b = rr.budget or {}
    root = rr.sections[0] if rr.sections else None
    prov = root.get("provenance", {}) if root else {}
    levels = ",".join(s["level"] for s in rr.sections) if rr.sections else None
    payload = b.get("serialized_tokens")
    eid = _new_event_id()
    ev = SemanticReadEvent(
        event_id=eid, channel=channel, source_event_key=source_event_key, session_id=session_id,
        stream_key=stream_key or session_id, request_id=request_id, ts=ts,
        repo_id=repo_id, path=prov.get("path"), symbol_id=rr.root,
        content_hash=prov.get("content_hash"),
        predicted_class=predicted_class, predicted_confidence=predicted_confidence,
        allowed=int(bool(allowed)), denied=int(not allowed), nudged=int(bool(nudged)),
        bypass_channel=bypass_channel,
        representation=levels,
        materialization_quality=(root.get("materialization_quality") if root else None),
        semantic_payload_tokens=payload,
        source_body_tokens=b.get("source_body_tokens"),
        protocol_overhead=b.get("protocol_overhead_ratio"),
        transport_content_tokens=payload, transport_overhead_tokens=0,
        budget_requested=b.get("requested"))
    store.put_semantic_read(ev)
    return eid


def record_expansion(store: GraphStore, exp, *, parent_event_id: Optional[str] = None,
                     session_id: Optional[str] = None, request_id: Optional[str] = None,
                     stream_key: Optional[str] = None, ts: Optional[str] = None,
                     from_level: Optional[str] = None, reason: Optional[str] = None,
                     repo_id: Optional[str] = None,
                     source_event_key: Optional[str] = None) -> Optional[str]:
    """Record an explicit expansion. `parent_event_id` is OPTIONAL — a materialization is
    logged whether or not it is attributed to a prior read (an unattributed expansion still
    consumed context). When a parent IS given, this expansion's transport tokens count toward
    that read's Context Expansion Debt. Each call is a distinct event (fresh UUID) unless a
    matching `source_event_key` already exists (intentional replay). Returns None only when
    nothing was materialized."""
    if not exp.found:
        return None
    if source_event_key:
        existing = store.semantic_read_by_source_key(source_event_key)
        if existing is not None:
            return existing["event_id"]
    tokens = est_tokens(exp.text)
    eid = _new_event_id()
    ev = SemanticReadEvent(
        event_id=eid, channel="expansion", source_event_key=source_event_key, session_id=session_id,
        stream_key=stream_key or session_id, request_id=request_id, ts=ts,
        repo_id=repo_id, symbol_id=exp.symbol_id,
        semantic_payload_tokens=tokens, source_body_tokens=tokens, protocol_overhead=0.0,
        transport_content_tokens=tokens, transport_overhead_tokens=0,
        parent_event_id=parent_event_id, from_level=from_level, to_level=exp.level,
        reason=reason)
    store.put_semantic_read(ev)
    return eid
