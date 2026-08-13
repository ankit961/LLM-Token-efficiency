"""Build the Context Residency Graph from a normalized session.

Phase 0b populates the residency backbone (design §5, §9):
  - Request nodes
  - ContextObject nodes (content-addressed), each with a RESIDENT_IN edge whose
    {entry_turn, exit_turn} span = residency until the next compaction / session end
  - DUPLICATE_OF edges when the same content is delivered again (Tier-A redundancy)
  - Source nodes + MATERIALIZED_FROM edges for reads/commands
  - CacheIsland nodes + BROKE edges (basic cache_read-collapse heuristic; ESTIMATE)

Deferred (tables exist, logic later): DEPENDS_ON (Phase 2), REDUCES/IN_CAPSULE (Phase 4).
"""
from __future__ import annotations

import bisect
from typing import Optional

from . import SCHEMA_VERSION
from .ingest import ContentEvent, load_session
from .model import CacheIsland, ContextObject, Request, Source, content_hash
from .redact import redact
from .store import GraphStore

BREAK_RATIO = 0.5
PREFIX_FLOOR = 20_000


def _segment_end(turn: int, seg_bounds: list[int], n_turns: int) -> int:
    """Residency horizon: content is resident until the next compaction or end."""
    i = bisect.bisect_right(seg_bounds, turn)
    return (seg_bounds[i] - 1) if i < len(seg_bounds) else (n_turns - 1)


def build(store: GraphStore, requests: list[Request], events: list[ContentEvent],
          seg_bounds: list[int]) -> None:
    n = len(requests)
    session_id = requests[0].session_id if requests else "session"

    # --- requests + basic cache islands / breaks (estimates) -----------------
    prev: Optional[Request] = None
    island_seq = 0
    cur_island: Optional[str] = None
    for r in requests:
        if prev is not None:
            established = prev.cache_read + prev.cache_creation
            broke = established >= PREFIX_FLOOR and r.cache_read < BREAK_RATIO * established
            if broke or cur_island is None:
                island_seq += 1
                cur_island = f"{session_id}::isl{island_seq}"
                store.put_island(CacheIsland(
                    island_id=cur_island, session_id=session_id, model=r.model,
                    established_turn=r.turn, size_tokens=r.cache_creation, state="warm",
                    schema_version=SCHEMA_VERSION))
                if broke:
                    store.add_edge(cur_island, r.request_id, "BROKE",
                                   {"cause": _break_cause(prev, r)}, session_id=session_id)
        elif cur_island is None:
            island_seq += 1
            cur_island = f"{session_id}::isl{island_seq}"
            store.put_island(CacheIsland(cur_island, session_id, r.model, r.turn,
                             r.cache_creation, "warm", schema_version=SCHEMA_VERSION))
        r.cache_island_id = cur_island
        store.put_request(r)
        prev = r

    # --- content objects + residency edges + dedup ---------------------------
    # Each DELIVERY is a distinct content-addressed node (content_hash + instance
    # id); the CAS (blobs) dedups the bytes. A re-delivery of identical content is a
    # new node linked by DUPLICATE_OF to the first instance (design §9 — Tier-A).
    seen: dict[str, str] = {}          # content_hash -> first instance content_id
    for idx, ev in enumerate(events):
        if ev.token_est <= 0:
            continue
        h = ev.hash
        cid = f"{session_id}::obj:{h[:12]}#{idx}"       # session-scoped, unique per delivery
        exit_turn = _segment_end(min(ev.entry_turn, n - 1), seg_bounds, n)
        trust = "external" if ev.provenance == "external" else ev.provenance
        nbytes = len(ev.text.encode("utf-8", "replace"))
        store.put_object(ContextObject(
            content_id=cid, session_id=session_id, content_hash=h, kind=ev.kind,
            token_est=ev.token_est, byte_size=nbytes, provenance=ev.provenance,
            trust_level=trust, first_seen_turn=ev.entry_turn, last_seen_turn=exit_turn,
            source_ref=ev.source_ref, schema_version=SCHEMA_VERSION))
        # CAS holds the raw payload behind the handle (design §9), bounded and
        # REDACTED before storage. Local + gitignored, never committed.
        store.put_blob(h, nbytes, redact(ev.text[:8000]))
        # RESIDENT_IN: object -> session, span = [entry, exit] within its segment
        store.add_edge(cid, session_id, "RESIDENT_IN",
                       {"entry_turn": ev.entry_turn, "exit_turn": exit_turn, "tier": "mixed"},
                       session_id=session_id)
        if ev.source_ref:
            store.put_source(Source(ev.source_ref, content_hash(ev.source_ref),
                             ev.tool_name or "tool", SCHEMA_VERSION))
            store.add_edge(cid, ev.source_ref, "MATERIALIZED_FROM", session_id=session_id)
        # DUPLICATE_OF: byte-identical content delivered again (design §9)
        if h in seen:
            store.add_edge(cid, seen[h], "DUPLICATE_OF", session_id=session_id)
        else:
            seen[h] = cid
    store.commit()


def _break_cause(prev: Request, cur: Request) -> str:
    if prev.model != cur.model:
        return "model_change"
    # TTL/compaction/version require timestamps/markers not modeled here yet.
    return "unknown"


def ingest_file(store: GraphStore, path) -> dict:
    """Convenience: load a transcript file and build its residency graph.

    Idempotent: re-ingesting the same session replaces its state rather than
    duplicating it (the experimental ledger must not drift on re-runs).
    """
    requests, events, seg_bounds = load_session(path)
    if requests:
        store.delete_session(requests[0].session_id)
    build(store, requests, events, seg_bounds)
    return {"requests": len(requests), "events": len(events),
            "segments": len(seg_bounds) + 1}
