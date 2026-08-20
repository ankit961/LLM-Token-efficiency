"""SQLite-backed Context Residency Graph store.

Nodes live in typed tables; every relationship is a row in a single ``edges``
catalog (design §9). The store is the only thing that touches the DB; ingest,
residency, and ledger are pure logic over it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import SCHEMA_VERSION
from .model import (
    CacheIsland, CodeSymbol, ContextObject, LedgerEvent, Request,
    SemanticReadEvent, Source,
)

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class GraphStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # Multiple MCP processes / hook invocations may share one on-disk store, and concurrent
        # PostToolUse writes race. busy_timeout makes a writer WAIT for a lock instead of failing;
        # WAL makes readers not block the writer, so a writer never gets SQLITE_BUSY ("database is
        # locked") just because another connection has an open read — the concurrency-safety fix
        # (C13; complements the AUTOINCREMENT seq). busy_timeout is set BEFORE switching to WAL so the
        # mode-switch itself waits for any lock. WAL is a no-op / ignored for :memory: stores.
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(_SCHEMA)
        self._check_schema_version()

    def _check_schema_version(self) -> None:
        cur = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()
        elif row["value"] != SCHEMA_VERSION:
            # Phase 0b: no migrations yet. Fail loudly rather than misinterpret.
            raise RuntimeError(
                f"store schema_version {row['value']} != package {SCHEMA_VERSION}; "
                "migration not implemented (design C13)"
            )

    # --- upserts -------------------------------------------------------------

    def put_object(self, o: ContextObject) -> None:
        self.conn.execute(
            """INSERT INTO objects VALUES
               (:content_id,:session_id,:content_hash,:kind,:token_est,:byte_size,
                :provenance,:trust_level,:first_seen_turn,:last_seen_turn,:source_ref,
                :reducer_applied,:schema_version)
               ON CONFLICT(content_id) DO UPDATE SET
                 last_seen_turn=max(last_seen_turn, excluded.last_seen_turn),
                 first_seen_turn=min(first_seen_turn, excluded.first_seen_turn)""",
            {**asdict(o), "reducer_applied": int(o.reducer_applied)},
        )

    def put_request(self, r: Request) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO requests VALUES
               (:request_id,:session_id,:turn,:model,:ts,:input_tokens,:cache_read,
                :cache_creation,:output_tokens,:cache_island_id,:measurement_quality,
                :schema_version)""",
            asdict(r),
        )

    def put_island(self, i: CacheIsland) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO islands VALUES
               (:island_id,:session_id,:model,:established_turn,:size_tokens,:state,
                :effective_window_estimate_min,:schema_version)""",
            asdict(i),
        )

    def put_source(self, s: Source) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sources VALUES (:source_ref,:source_hash,:kind,:schema_version)",
            asdict(s),
        )

    def put_blob(self, content_hash: str, byte_size: int, sample: Optional[str]) -> None:
        # sample is expected to be redaction-scrubbed by the caller (residency).
        self.conn.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?,?,?)",
            (content_hash, byte_size, sample),
        )

    def add_edge(self, src_id: str, dst_id: str, edge_type: str,
                 props: Optional[dict] = None, session_id: Optional[str] = None) -> None:
        # Idempotent: re-ingesting the same transcript cannot duplicate an edge
        # (UNIQUE(src_id,dst_id,edge_type)).
        self.conn.execute(
            "INSERT OR IGNORE INTO edges(session_id,src_id,dst_id,edge_type,props) "
            "VALUES (?,?,?,?,?)",
            (session_id, src_id, dst_id, edge_type, json.dumps(props) if props else None),
        )

    def delete_session(self, session_id: str) -> None:
        """Remove all state for a session so re-ingest is idempotent."""
        c = self.conn
        c.execute("DELETE FROM edges    WHERE session_id=?", (session_id,))
        c.execute("DELETE FROM objects  WHERE session_id=?", (session_id,))
        c.execute("DELETE FROM requests WHERE session_id=?", (session_id,))
        c.execute("DELETE FROM islands  WHERE session_id=?", (session_id,))

    def blob(self, content_hash: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM blobs WHERE content_hash=?", (content_hash,)).fetchone()

    # --- CodeSymbol graph (repo-scoped) --------------------------------------

    def put_symbol(self, s: CodeSymbol) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO symbols VALUES
               (:symbol_id,:repo_id,:language,:kind,:qualified_name,:path,:start_line,
                :end_line,:signature,:content_hash,:parser,:resolution_quality,
                :schema_version)""",
            asdict(s),
        )

    def add_code_edge(self, repo_id: str, src_id: str, dst_id: str, edge_type: str,
                      confidence: float, resolution: str, match_kind: str = "na",
                      ambiguity_count: int = 0, props: Optional[dict] = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO code_edges(repo_id,src_id,dst_id,edge_type,"
            "confidence,resolution,match_kind,ambiguity_count,props) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (repo_id, src_id, dst_id, edge_type, confidence, resolution,
             match_kind, ambiguity_count, json.dumps(props) if props else None),
        )

    def delete_repo(self, repo_id: str) -> None:
        """Idempotent re-index: drop a repo's symbols + code edges first."""
        self.conn.execute("DELETE FROM code_edges WHERE repo_id=?", (repo_id,))
        self.conn.execute("DELETE FROM symbols    WHERE repo_id=?", (repo_id,))

    def symbols(self, repo_id: Optional[str] = None) -> Iterator[sqlite3.Row]:
        if repo_id:
            yield from self.conn.execute("SELECT * FROM symbols WHERE repo_id=?", (repo_id,))
        else:
            yield from self.conn.execute("SELECT * FROM symbols")

    def code_edges(self, edge_type: Optional[str] = None) -> Iterator[sqlite3.Row]:
        if edge_type:
            yield from self.conn.execute(
                "SELECT * FROM code_edges WHERE edge_type=?", (edge_type,))
        else:
            yield from self.conn.execute("SELECT * FROM code_edges")

    def has_symbol(self, symbol_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM symbols WHERE symbol_id=? LIMIT 1", (symbol_id,)).fetchone() is not None

    def symbol_row(self, symbol_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM symbols WHERE symbol_id=?", (symbol_id,)).fetchone()

    def find_symbol(self, qualified_name: str, repo_id: Optional[str] = None) -> Optional[sqlite3.Row]:
        if repo_id:
            return self.conn.execute(
                "SELECT * FROM symbols WHERE qualified_name=? AND repo_id=? LIMIT 1",
                (qualified_name, repo_id)).fetchone()
        return self.conn.execute(
            "SELECT * FROM symbols WHERE qualified_name=? LIMIT 1", (qualified_name,)).fetchone()

    def code_edges_from(self, src_id: str, edge_types: Optional[tuple] = None) -> list:
        if edge_types:
            q = ("SELECT * FROM code_edges WHERE src_id=? AND edge_type IN (%s)"
                 % ",".join("?" * len(edge_types)))
            return self.conn.execute(q, (src_id, *edge_types)).fetchall()
        return self.conn.execute("SELECT * FROM code_edges WHERE src_id=?", (src_id,)).fetchall()

    def code_edges_to(self, dst_id: str, edge_types: Optional[tuple] = None) -> list:
        if edge_types:
            q = ("SELECT * FROM code_edges WHERE dst_id=? AND edge_type IN (%s)"
                 % ",".join("?" * len(edge_types)))
            return self.conn.execute(q, (dst_id, *edge_types)).fetchall()
        return self.conn.execute("SELECT * FROM code_edges WHERE dst_id=?", (dst_id,)).fetchall()

    def search_symbols(self, query: str, repo_id: Optional[str] = None, limit: int = 10) -> list:
        """Cheap structural search — exact qualified-name, short-name, path substring.
        No embeddings (design: keep Phase-2 clean). Returns symbol rows."""
        like = f"%{query}%"
        params = [query, f"%.{query}", like, like]
        where = "(qualified_name=? OR qualified_name LIKE ? OR qualified_name LIKE ? OR path LIKE ?)"
        if repo_id:
            where += " AND repo_id=?"
            params.append(repo_id)
        rows = self.conn.execute(
            f"SELECT * FROM symbols WHERE {where} ORDER BY length(qualified_name) LIMIT ?",
            (*params, limit)).fetchall()
        return rows

    # --- SemanticReadEvent telemetry (Phase 2.4) -----------------------------

    def put_semantic_read(self, e: SemanticReadEvent) -> str:
        # Returns the CANONICAL persisted event_id — the id actually in the store — computed
        # ATOMICALLY, so concurrent replays of one producer event never return a UUID that lost
        # the insert race and was never persisted (which would orphan a later parent_event_id).
        #
        # `seq` is DB-ASSIGNED (AUTOINCREMENT): inserting NULL lets SQLite allocate it, so two
        # MCP processes can't collide on ordering. Idempotence is INTENTIONAL only: a duplicate
        # producer key (source_system, stream_key, source_event_key) is dropped; a duplicate
        # `event_id` is an ACCIDENT and must fail loudly — so we scope the conflict clause to the
        # producer-key index and let an event_id collision raise.
        d = asdict(e)
        cols = ",".join(d.keys())
        ph = ",".join(f":{k}" for k in d.keys())
        self.conn.execute(
            f"INSERT INTO semantic_reads ({cols}) VALUES ({ph}) "
            "ON CONFLICT(source_system, stream_key, source_event_key) DO NOTHING", d)
        if e.source_event_key is None:
            return e.event_id                    # unkeyed: this UUID is the only identity
        # Keyed: read back whichever row won the producer key — this insert's, or a prior one's.
        row = self.conn.execute(
            "SELECT event_id FROM semantic_reads "
            "WHERE source_system=? AND stream_key=? AND source_event_key=?",
            (e.source_system, e.stream_key, e.source_event_key)).fetchone()
        return row["event_id"] if row else e.event_id

    def update_transport_tokens(self, event_id: str, transport_content_tokens: int) -> None:
        """Record the FULL transport response size (semantic payload + transport meta block)
        and derive the transport-layer overhead. Called by the transport once it has built
        the actual response the model will see."""
        row = self.semantic_read(event_id)
        if row is None:
            return
        payload = row["semantic_payload_tokens"] or 0
        overhead = max(0, transport_content_tokens - payload)
        self.conn.execute(
            "UPDATE semantic_reads SET transport_content_tokens=?, transport_overhead_tokens=? "
            "WHERE event_id=?", (transport_content_tokens, overhead, event_id))

    def semantic_read(self, event_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM semantic_reads WHERE event_id=?", (event_id,)).fetchone()

    def semantic_reads(self, session_id: Optional[str] = None,
                       channel: Optional[str] = None) -> list:
        where, params = [], []
        if session_id:
            where.append("session_id=?"); params.append(session_id)
        if channel:
            where.append("channel=?"); params.append(channel)
        q = "SELECT * FROM semantic_reads"
        if where:
            q += " WHERE " + " AND ".join(where)
        return self.conn.execute(q + " ORDER BY seq", params).fetchall()

    def context_expansion_debt(self, event_id: str) -> int:
        """CED for a read = FULL transport tokens of the expansions it caused (the model
        receives the transport meta too), falling back to the semantic payload for a channel
        that emitted no transport measurement (e.g. an attributed native read)."""
        return int(self.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(transport_content_tokens, semantic_payload_tokens)), 0) AS t "
            "FROM semantic_reads WHERE parent_event_id=? AND channel='expansion'",
            (event_id,)).fetchone()["t"])

    def commit(self) -> None:
        self.conn.commit()

    # --- reads ---------------------------------------------------------------

    def objects(self) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute("SELECT * FROM objects")

    def requests(self) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute("SELECT * FROM requests ORDER BY session_id, turn")

    def edges(self, edge_type: Optional[str] = None) -> Iterator[sqlite3.Row]:
        if edge_type:
            yield from self.conn.execute("SELECT * FROM edges WHERE edge_type=?", (edge_type,))
        else:
            yield from self.conn.execute("SELECT * FROM edges")

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]

    def edge_count(self, edge_type: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE edge_type=?", (edge_type,)
        ).fetchone()["c"]

    def close(self) -> None:
        self.conn.close()
