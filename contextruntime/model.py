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
# IN_CAPSULE is Phase 4 — defined, not yet populated.
EDGE_TYPES = (
    "RESIDENT_IN", "MATERIALIZED_FROM", "DUPLICATE_OF", "SUPERSEDES",
    "REDUCES", "CACHES", "BROKE", "IN_CAPSULE",
)

# CodeSymbol graph (Graph-Lite, C1) — repo-scoped.
SYMBOL_KINDS = ("module", "class", "interface", "function", "method",
                "type", "constant", "test")
CODE_EDGE_TYPES = ("CONTAINS", "IMPORTS", "REFERENCES", "CALLS",
                   "IMPLEMENTS", "TESTED_BY", "DEPENDS_ON")
# Provenance of a resolved edge — drives per-language bundle-quality (design C3).
RESOLUTION = ("compiler", "lsp", "scip", "python_ast", "tree_sitter",
              "regex_heuristic", "derived")

# Measurement quality (design §4.4) — JSONL is load-bearing; others are opt-in.
MEASUREMENT_QUALITY = ("exact", "reconciled", "estimated")


def content_hash(text: str) -> str:
    """Stable content address for a piece of context (design §9)."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


# --- nodes -------------------------------------------------------------------

@dataclass
class ContextObject:
    """One unit of potential model-visible content — the polymorphic core node.

    content_id is session-scoped ("<session>::obj:<hash>#<idx>") so identical
    content in different sessions never collides.
    """
    content_id: str
    session_id: str
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
    session_id: str
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
class CodeSymbol:
    """A code symbol node in the repo-scoped Graph-Lite (design §8/§9, C1).

    resolution_quality is the structural confidence of the parser that produced it
    (python_ast ~0.95, tree_sitter ~0.9, regex_heuristic ~0.6), so downstream bundle
    generation can widen the neighborhood for low-confidence languages (C3).
    """
    symbol_id: str
    repo_id: str
    language: str
    kind: str                  # one of SYMBOL_KINDS
    qualified_name: str
    path: str
    start_line: Optional[int]
    end_line: Optional[int]
    signature: Optional[str]
    content_hash: Optional[str]
    parser: str                # python_ast | tree_sitter | regex_heuristic
    resolution_quality: float
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


@dataclass
class SemanticReadEvent:
    """One model-visible materialization (design v1.2, Phase 2.4) — a SemanticFS read, a
    native/Bash read the classifier attributes, or an explicit expansion. Observe-only:
    `allowed=1` and the classification/outcome fields stay null until 2.4-C/D fill them.
    Expansion rows carry `parent_event_id` so Context Expansion Debt sums directly.
    """
    event_id: str                          # fresh per materialization (a UUID); accidental collision fails loudly
    channel: str                           # native_read | bash_materialization | semanticfs | expansion
    seq: Optional[int] = None              # DB-ASSIGNED (AUTOINCREMENT); leave None on insert
    source_system: Optional[str] = None    # producer ID domain (claude_mcp | transcript | hook)
    source_event_key: Optional[str] = None  # producer identity for INTENTIONAL replay idempotence, per (system, stream)
    session_id: Optional[str] = None
    stream_key: Optional[str] = None       # (session_id, agent_id) sub-stream
    request_id: Optional[str] = None
    ts: Optional[str] = None               # caller-supplied ISO timestamp (no wall-clock here)
    # target
    repo_id: Optional[str] = None
    path: Optional[str] = None
    symbol_id: Optional[str] = None
    content_hash: Optional[str] = None
    range_start: Optional[int] = None
    range_end: Optional[int] = None
    # classification (2.4-C/D)
    predicted_class: Optional[str] = None
    predicted_confidence: Optional[float] = None
    observed_class: Optional[str] = None
    classification_source: Optional[str] = None   # client_tracker_confirmed | temporal_causal | heuristic
    evidence_grade: Optional[str] = None
    # admission (observe-only now)
    allowed: int = 1
    denied: int = 0
    nudged: int = 0
    bypass_channel: Optional[str] = None
    # context — two overhead layers tracked distinctly (semantic vs transport)
    representation: Optional[str] = None
    materialization_quality: Optional[str] = None
    semantic_payload_tokens: Optional[int] = None    # SemanticFS serialized payload
    source_body_tokens: Optional[int] = None         # pure source body
    protocol_overhead: Optional[float] = None        # semantic-layer overhead ratio
    transport_content_tokens: Optional[int] = None   # full transport response (payload + meta)
    transport_overhead_tokens: Optional[int] = None  # transport_content - semantic_payload
    budget_requested: Optional[int] = None
    # expansion linkage (Context Expansion Debt)
    parent_event_id: Optional[str] = None
    from_level: Optional[str] = None
    to_level: Optional[str] = None
    reason: Optional[str] = None
    # outcome (2.4-C retrospective)
    later_edited: Optional[int] = None
    turns_to_edit: Optional[int] = None
    expanded_later: Optional[int] = None
    expansion_tokens: Optional[int] = None
    recovery_turns: Optional[int] = None
    schema_version: str = SCHEMA_VERSION
