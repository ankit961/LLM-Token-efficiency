"""Durable object model for the Context Residency Graph.

Every durable object carries a ``schema_version`` (design decision C13): we build
against fast-moving Claude/Codex/Cursor interfaces, so persisted state must survive
runtime upgrades without ambiguous reinterpretation.

Phase 0b populates: ContextObject, Request, CacheIsland (basic), Source, LedgerEvent,
CapabilityProfile. Capsule / EvidenceNode are defined here (schema-versioned) but
their edges are populated in Phase 4 — the tables exist now so the shape is frozen.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from . import SCHEMA_VERSION

# --- controlled vocabularies -------------------------------------------------

# ContextObject.kind — everything that can become model-visible content.
KINDS = (
    "system_prompt", "tool_def", "rule", "source_slice", "symbol",
    "tool_result", "test_result", "log", "search_result",
    "history_summary", "user_msg", "assistant_msg", "capsule_ref",
)

# Provenance / trust (design §10). External content is DATA, never instruction.
PROVENANCE = ("user", "source", "test", "tool", "model", "external")

# Edge types of the Context-Object Graph (design §9).
# DEPENDS_ON is Phase 2; IN_CAPSULE is Phase 4 — defined, not yet populated.
EDGE_TYPES = (
    "RESIDENT_IN", "MATERIALIZED_FROM", "DUPLICATE_OF", "SUPERSEDES",
    "REDUCES", "CACHES", "BROKE", "IN_CAPSULE", "DEPENDS_ON",
)

# Measurement quality (design §4.4) — JSONL is load-bearing; others are opt-in.
MEASUREMENT_QUALITY = ("exact", "reconciled", "estimated")


def content_hash(text: str) -> str:
    """Stable content address for a piece of context (design §9)."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


# --- nodes -------------------------------------------------------------------

@dataclass
class ContextObject:
    """One unit of potential model-visible content — the polymorphic core node."""
    content_id: str            # == content_hash for now (content-addressed)
    content_hash: str
    kind: str                  # one of KINDS
    token_est: int
    byte_size: int
    provenance: str            # one of PROVENANCE
    trust_level: str           # mirrors provenance for now; external => data-only
    first_seen_turn: int
    last_seen_turn: int
    source_ref: Optional[str] = None      # -> Source.source_ref (MATERIALIZED_FROM)
    reducer_applied: bool = False
    schema_version: str = SCHEMA_VERSION


@dataclass
class Request:
    """One reconciled model API call (a turn)."""
    request_id: str
    session_id: str
    turn: int
    model: str
    ts: Optional[str]
    input_tokens: int
    cache_read: int
    cache_creation: int
    output_tokens: int
    cache_island_id: Optional[str] = None
    measurement_quality: str = "reconciled"
    schema_version: str = SCHEMA_VERSION

    @property
    def occupancy(self) -> int:
        """Attention burden = everything the model had to hold (design §4.1)."""
        return self.input_tokens + self.cache_read + self.cache_creation


@dataclass
class CacheIsland:
    """Inferred unit of prefix reuse for one (model, prefix-lineage).

    All island / window / BROKE figures are ESTIMATES, never measured occupancy
    (design §2.3, §6): the real cache key is prefix bytes the JSONL does not expose.
    """
    island_id: str
    model: str
    established_turn: int
    size_tokens: int
    state: str = "warm"                 # warm | broken
    effective_window_estimate_min: Optional[float] = None  # measured, not assumed
    schema_version: str = SCHEMA_VERSION


@dataclass
class Source:
    """The underlying artifact an object was materialized from."""
    source_ref: str            # file@commit#symbol | tool+args_hash | mcp_uri
    source_hash: str
    kind: str
    schema_version: str = SCHEMA_VERSION


@dataclass
class Capsule:
    """Portable task checkpoint (design §11). Phase 4 — shape frozen now."""
    task_id: str
    mode: str = "continuation"          # continuation | review | debug | audit
    checkpoint_ref: Optional[str] = None
    evidence_ref: Optional[str] = None
    schema_version: str = SCHEMA_VERSION


@dataclass
class EvidenceNode:
    """A claim whose status is DERIVED from its edges + evidence staleness
    (design C4). Phase 4 — shape frozen now, not yet populated."""
    claim_id: str
    statement_hash: str
    # status is NOT stored: it is derived at read time from VERIFIED_BY /
    # SUPPORTED_BY / ASSERTED_BY / CONTRADICTED_BY edges and evidence freshness.
    schema_version: str = SCHEMA_VERSION


@dataclass
class LedgerEvent:
    """A priced accounting row for one request (design §4)."""
    request_id: str
    occupancy_tokens: int
    uncached_input: int
    cache_read: int
    cache_creation: int
    output_tokens: int
    est_cost_usd: float
    measurement_quality: str = "reconciled"
    schema_version: str = SCHEMA_VERSION


@dataclass
class CapabilityProfile:
    """Result of the ContextRuntime Doctor probe (design C11). Stamped on every
    ledger/benchmark report so numbers from different clients are comparable."""
    client: str
    client_version: Optional[str]
    capabilities: dict = field(default_factory=dict)   # name -> "yes"|"no"|"?"
    reduction_mode: str = "unknown"        # FULL | MCP_ONLY | NONE
    admission_mode: str = "unknown"        # ENFORCED | BEST_EFFORT | ADVISORY
    evidence_grade: str = "C"              # A | B | C
    schema_version: str = SCHEMA_VERSION
