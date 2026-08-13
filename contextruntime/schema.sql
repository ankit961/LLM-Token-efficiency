-- Context Residency Graph — SQLite schema (design v1.2 §9).
-- Graph-Lite is mandatory runtime infrastructure (C1): SQLite edge tables, no Neo4j.
-- The model NEVER receives this graph; it receives a flat manifest + reduced slices.

PRAGMA foreign_keys = ON;

-- schema_version of the store itself (in addition to per-row schema_version, C13).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Content-addressed blob store (CAS): raw payloads live behind handles, never in
-- the prompt. Phase 0b stores estimates; full-byte capture is a runtime concern.
CREATE TABLE IF NOT EXISTS blobs (
    content_hash TEXT PRIMARY KEY,
    byte_size    INTEGER NOT NULL,
    sample       TEXT                     -- bounded, redaction-safe preview only
);

CREATE TABLE IF NOT EXISTS objects (
    content_id      TEXT PRIMARY KEY,     -- session-scoped: "<session>::obj:<hash>#<idx>"
    session_id      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    token_est       INTEGER NOT NULL,
    byte_size       INTEGER NOT NULL,
    provenance      TEXT NOT NULL,
    trust_level     TEXT NOT NULL,
    first_seen_turn INTEGER NOT NULL,
    last_seen_turn  INTEGER NOT NULL,
    source_ref      TEXT,
    reducer_applied INTEGER NOT NULL DEFAULT 0,
    schema_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_hash    ON objects(content_hash);
CREATE INDEX IF NOT EXISTS idx_objects_kind    ON objects(kind);
CREATE INDEX IF NOT EXISTS idx_objects_session ON objects(session_id);

CREATE TABLE IF NOT EXISTS requests (
    request_id          TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    turn                INTEGER NOT NULL,
    model               TEXT NOT NULL,
    ts                  TEXT,
    input_tokens        INTEGER NOT NULL,
    cache_read          INTEGER NOT NULL,
    cache_creation      INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    cache_island_id     TEXT,
    measurement_quality TEXT NOT NULL,
    schema_version      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id, turn);

CREATE TABLE IF NOT EXISTS islands (
    island_id                    TEXT PRIMARY KEY,     -- "<session>::isl<n>"
    session_id                   TEXT NOT NULL,
    model                        TEXT NOT NULL,
    established_turn             INTEGER NOT NULL,
    size_tokens                  INTEGER NOT NULL,
    state                        TEXT NOT NULL,
    effective_window_estimate_min REAL,
    schema_version               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_ref     TEXT PRIMARY KEY,
    source_hash    TEXT NOT NULL,
    kind           TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

-- CodeSymbol graph (design v1.2 §8/§9, Graph-Lite C1) — repo-scoped, shared across
-- sessions. Symbols are nodes; code_edges is the repo-scoped edge catalog, and every
-- edge carries a confidence + resolution provenance so a dependency bundle never
-- pretends all languages have equally sound analysis.
CREATE TABLE IF NOT EXISTS symbols (
    symbol_id          TEXT PRIMARY KEY,   -- "<repo>::<path>::<qualified_name>"
    repo_id            TEXT NOT NULL,
    language           TEXT NOT NULL,
    kind               TEXT NOT NULL,      -- module/class/interface/function/method/type/constant/test
    qualified_name     TEXT NOT NULL,
    path               TEXT NOT NULL,
    start_line         INTEGER,
    end_line           INTEGER,
    signature          TEXT,
    content_hash       TEXT,
    parser             TEXT NOT NULL,      -- python_ast | tree_sitter | regex_heuristic
    resolution_quality REAL NOT NULL,      -- structural confidence of the parser
    schema_version     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_repo ON symbols(repo_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_lang ON symbols(language);

CREATE TABLE IF NOT EXISTS code_edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id    TEXT NOT NULL,
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,              -- symbol_id, or "unresolved:<name>"
    edge_type  TEXT NOT NULL,              -- CONTAINS/IMPORTS/REFERENCES/CALLS/IMPLEMENTS/TESTED_BY/DEPENDS_ON
    confidence REAL NOT NULL,
    resolution TEXT NOT NULL,              -- python_ast | tree_sitter | regex_heuristic | derived
    props      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cedges_uniq ON code_edges(src_id, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_cedges_repo ON code_edges(repo_id);
CREATE INDEX IF NOT EXISTS idx_cedges_src  ON code_edges(src_id, edge_type);

-- Phase 4 tables — shape frozen now (C13), populated later.
CREATE TABLE IF NOT EXISTS capsules (
    task_id        TEXT PRIMARY KEY,
    mode           TEXT NOT NULL,
    checkpoint_ref TEXT,
    evidence_ref   TEXT,
    schema_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_nodes (
    claim_id       TEXT PRIMARY KEY,
    statement_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

-- The single authoritative edge catalog (design §9). props is JSON, e.g.
-- RESIDENT_IN {entry_turn, exit_turn, tier}; BROKE {cause}; REDUCES {handle}.
-- session_id enables O(1) delete-by-session for idempotent re-ingest.
-- The UNIQUE index makes edge writes idempotent (re-ingest cannot duplicate).
CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    src_id    TEXT NOT NULL,
    dst_id    TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    props     TEXT                        -- JSON
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_uniq ON edges(src_id, dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_src     ON edges(src_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst     ON edges(dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_type    ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_session ON edges(session_id);
