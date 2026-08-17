"""B1.0 — the live reducer CAS (Transparent Reduction Contract v0.1 §3, safety §4.2).

The reducer replaces a large tool output with a compact summary plus a `result://<hash>`
handle. That handle is only *safe* if it is genuinely recoverable — otherwise reducing
below the budget floor loses information (violates safety invariant #2). In batch mode
the CAS is populated by `ingest`; in a LIVE session nothing did, so every emitted handle
was a dangling pointer. This module is the live-session CAS that closes that gap.

It is a DEDICATED store, deliberately separate from the residency GraphStore and from any
frozen benchmark artifact:
  - its own path (`CR_DB` env, else ``~/.contextruntime/live.db``) — resolvable from any
    project cwd, which is also the foreign-cwd fix (§3.4);
  - its own schema (a `created_at` column the frozen `blobs` schema does not have), so
    TTL/size eviction never requires bumping the immutable-store SCHEMA_VERSION;
  - redacted at rest and bounded, exactly like the batch CAS.

Fail-open: every operation swallows its own errors. A CAS write that fails must never
abort a tool turn — at worst the handle is unrecoverable, which the reducer already has
to tolerate (the model re-runs the source op), so the safe degradation is identical.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..ingest import content_hash
from ..redact import redact

# Bounds. Generous enough that essentially every grep/find/ls payload round-trips whole;
# a pathological multi-MB output is capped and flagged `truncated` so recovery is honest.
MAX_SAMPLE_BYTES = 200_000          # ~50k tokens; stored payload ceiling
TTL_SECONDS = 24 * 3600             # a handle older than this is assumed stale
MAX_ROWS = 20_000                   # hard row cap; oldest evicted first

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_blobs (
    content_hash   TEXT PRIMARY KEY,
    reducer        TEXT,
    representation TEXT,
    stored_bytes   INTEGER NOT NULL,
    full_bytes     INTEGER NOT NULL,
    truncated      INTEGER NOT NULL,   -- 0/1
    sample         TEXT,               -- redacted, capped payload behind the handle
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_live_blobs_created ON live_blobs(created_at);
"""


@dataclass
class Recovered:
    handle: str
    found: bool
    text: str = ""
    truncated: bool = False
    full_bytes: int = 0
    stored_bytes: int = 0
    age_seconds: Optional[float] = None
    note: str = ""


@dataclass
class Stored:
    """Outcome of a live-CAS write. `persisted` = the payload is verifiably in the CAS
    (read back after commit); `exact` = recovery returns the raw payload VERBATIM — i.e. it
    was neither truncated NOR altered by redaction. The reducer must replace model-visible
    output ONLY when BOTH persisted AND exact hold; a truncated or redacted store is a bounded/
    sanitized recovery, not the "full output" the summary claims, so it passes through instead.
    (`redacted` is surfaced so a future `sanitized://` scheme can be distinguished from
    `result://` without conflating them in B1.)"""
    handle: str
    persisted: bool
    exact: bool
    truncated: bool = False
    redacted: bool = False
    full_bytes: int = 0
    stored_bytes: int = 0
    note: str = ""


def decision_log_path() -> str:
    """CR_DECISION_LOG if set, else ~/.contextruntime/decisions.jsonl. This is the durable,
    append-only record the offline replay (B1 step 4) reads to measure what the reducer actually
    captured live — raw vs reduced tokens per gated call, the reducer used, the reason, the handle,
    and whether enforcement actually fired."""
    p = os.environ.get("CR_DECISION_LOG")
    path = Path(p).expanduser() if p else Path.home() / ".contextruntime" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def log_decision(record: dict, *, path: Optional[str] = None,
                 now: Optional[float] = None) -> None:
    """Append one JSON decision record. Fail-open: a logging error never affects the tool turn."""
    try:
        record = {"ts": time.time() if now is None else now, **record}
        with open(path or decision_log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:                       # noqa: BLE001 — observability must never break a turn
        pass


def live_db_path() -> str:
    """CR_DB if set, else ~/.contextruntime/live.db. Parent dir is created on demand."""
    p = os.environ.get("CR_DB")
    if p:
        path = Path(p).expanduser()
    else:
        path = Path.home() / ".contextruntime" / "live.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or live_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")   # concurrent hooks wait, don't fail
    conn.executescript(_SCHEMA)
    return conn


def _blob_hash(handle: str) -> Optional[str]:
    for prefix in ("result://", "ctx://blob/"):
        if handle.startswith(prefix):
            return handle[len(prefix):]
    return None


def _evict(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM live_blobs WHERE created_at < ?", (now - TTL_SECONDS,))
    over = conn.execute("SELECT COUNT(*) AS c FROM live_blobs").fetchone()["c"] - MAX_ROWS
    if over > 0:
        conn.execute(
            "DELETE FROM live_blobs WHERE content_hash IN "
            "(SELECT content_hash FROM live_blobs ORDER BY created_at ASC LIMIT ?)",
            (over,))


def put_confirmed(raw: str, *, reducer: str = "", representation: str = "",
                  now: Optional[float] = None, path: Optional[str] = None) -> Stored:
    """Store the (redacted, bounded) raw payload and CONFIRM the write by reading the row
    back after commit. Returns a `Stored` whose `persisted`/`exact` flags the caller must
    check before replacing model-visible output.

    The hash is `content_hash(raw)` — identical to `reducers.base.make_handle`, so the
    handle the reducer already embeds in its summary resolves here by construction. Every
    failure path returns `persisted=False` (never raises), so the strict caller degrades to
    passing the raw output through — the safe direction."""
    handle = f"result://{content_hash(raw)}"
    now = time.time() if now is None else now
    try:
        h = content_hash(raw)
        raw_bytes = raw.encode("utf-8", "replace")
        full_bytes = len(raw_bytes)
        # BYTE-accurate cap (not a char slice): bound the stored bytes, on a valid char boundary.
        if full_bytes > MAX_SAMPLE_BYTES:
            truncated = True
            capped = raw_bytes[:MAX_SAMPLE_BYTES].decode("utf-8", "ignore")
            sample = redact(capped)
        else:
            truncated = False
            sample = redact(raw)
        # EXACT recovery ⟺ the stored sample equals the raw payload verbatim: not truncated AND
        # redaction was a no-op. If redaction rewrote a secret, recovery returns modified text, so
        # the "[+ full output]" claim would be false — the reducer must then pass through.
        redacted = (sample != raw)
        stored_bytes = len(sample.encode("utf-8", "replace"))
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO live_blobs "
                "(content_hash, reducer, representation, stored_bytes, full_bytes, "
                " truncated, sample, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (h, reducer, representation, stored_bytes, full_bytes, int(truncated), sample, now))
            _evict(conn, now)
            conn.commit()
            # Verify the payload is actually retrievable — a swallowed write error must NOT
            # look like a successful persist (this is the fix for the dead-handle blocker).
            row = conn.execute(
                "SELECT 1 FROM live_blobs WHERE content_hash=?", (h,)).fetchone()
        finally:
            conn.close()
        persisted = row is not None
        exact = persisted and not truncated and not redacted
        note = "stored (exact)" if exact else (
            "not persisted" if not persisted else
            f"stored but {'BOUNDED' if truncated else 'REDACTED'} — recovery not verbatim, not exact")
        return Stored(handle=handle, persisted=persisted, exact=exact, truncated=truncated,
                      redacted=redacted, full_bytes=full_bytes, stored_bytes=stored_bytes, note=note)
    except Exception as e:                  # noqa: BLE001 — fail closed on recovery: report failure
        return Stored(handle=handle, persisted=False, exact=False, note=f"CAS write failed: {e}")


def put(raw: str, *, reducer: str = "", representation: str = "",
        now: Optional[float] = None, path: Optional[str] = None) -> str:
    """Back-compat wrapper: store and return just the handle (see `put_confirmed` for the
    persistence/exactness status the strict reducer path requires)."""
    return put_confirmed(raw, reducer=reducer, representation=representation,
                         now=now, path=path).handle


def resolve(handle: str, *, now: Optional[float] = None,
            path: Optional[str] = None) -> Recovered:
    """Resolve a `result://` / `ctx://blob/` handle from the live CAS. Never a silent
    empty: an unknown or expired handle is reported so the model re-runs rather than loops."""
    h = _blob_hash(handle)
    if h is None:
        return Recovered(handle, False, note="unrecognized handle scheme")
    now = time.time() if now is None else now
    try:
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT * FROM live_blobs WHERE content_hash=?", (h,)).fetchone()
        finally:
            conn.close()
    except Exception as e:                  # noqa: BLE001 — fail open
        return Recovered(handle, False, note=f"live CAS unavailable: {e}")
    if row is None:
        return Recovered(handle, False, note="not in live CAS (never stored, or evicted)")
    age = now - row["created_at"]
    if age > TTL_SECONDS:
        return Recovered(handle, False, age_seconds=age,
                         note="expired — re-run the source operation")
    note = "recovered from live CAS"
    if row["truncated"]:
        note += f" (bounded: {row['stored_bytes']} of {row['full_bytes']} bytes)"
    return Recovered(handle, True, text=row["sample"] or "", truncated=bool(row["truncated"]),
                     full_bytes=row["full_bytes"], stored_bytes=row["stored_bytes"],
                     age_seconds=age, note=note)
