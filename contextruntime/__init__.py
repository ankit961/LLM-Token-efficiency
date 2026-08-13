"""ContextRuntime — semantic I/O + context-control runtime for AI coding agents.

Phase 0b: ContextScope + the Context Residency Graph.

This package is the incremental, graph-backed evolution of the one-shot batch
profilers under ``contextscope/``. It ingests Claude Code transcripts into a
content-addressed residency graph (SQLite) and computes the occupancy and
economic ledgers as queries over that graph.

Design reference: ContextRuntime Design Document v1.2, sections 5 (ContextScope +
Context Residency Graph) and 9 (the Context-Object Graph data model).
"""

__version__ = "0.1.0"

# Bump when the on-disk durable schema changes in a backward-incompatible way.
# Every durable object also carries its own schema_version (design C13).
SCHEMA_VERSION = "0.1.0"
