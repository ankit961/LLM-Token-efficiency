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
    CacheIsland, CodeSymbol, ContextObject, LedgerEvent, Request, Source,
)

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class GraphStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
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
                      confidence: float, resolution: str,
                      props: Optional[dict] = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO code_edges(repo_id,src_id,dst_id,edge_type,"
            "confidence,resolution,props) VALUES (?,?,?,?,?,?,?)",
            (repo_id, src_id, dst_id, edge_type, confidence, resolution,
             json.dumps(props) if props else None),
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
