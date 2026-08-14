"""HookJournal -- prospective observation layer (Phase 2.4-C), wired to the REAL Claude Code hook
contract. A SEPARATE SQLite store (hook_schema 0.2.0), never the frozen B.1 GraphStore.

Contract notes that make this correct against Claude Code (not a synthetic shape):
  - the event kind is `hook_event_name` (the field `event` belongs to FileChanged: change/add/unlink);
  - `PostToolBatch` carries a `tool_calls` array, each with tool_use_id/tool_name/tool_input and the
    SERIALIZED, model-visible `tool_response` -- that is the honest token denominator;
  - each hook delivery is a SEPARATE process invocation (JSON on stdin), so cross-event state
    (step epoch, pending pre-hashes, processed batches) is PERSISTED here, not held in memory;
  - a `PostToolUseFailure` means the tool failed (its payload has an `error`) -- a failed read
    materialized nothing, but a failed op that changed bytes is still a mutation boundary.

Captures are METADATA-ONLY (hashes + counts) -- never raw file/edit/command contents. FAIL-OPEN,
but never INVISIBLY: a capture error is logged to `capture_errors` so a coverage ratio is reportable.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from typing import Callable, Optional, Tuple

from .ingest import est_tokens
from .normalize import BASH_MATERIALIZATION, NATIVE_READ, bash_effects

HOOK_SCHEMA_VERSION = "0.2.0"
MAX_HASH_BYTES = 32 * 1024 * 1024          # above this a file is `oversize`, not hashed

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    session_id TEXT, agent_id TEXT, stream_key TEXT, prompt_id TEXT, cwd TEXT,
    step INTEGER,
    batch_id TEXT, batch_size INTEGER, parallel INTEGER,
    tool_use_id TEXT, tool_name TEXT,
    kind TEXT, channel TEXT, mutation_source TEXT, representation TEXT,
    path_absolute TEXT, path_normalized TEXT, repo_relative TEXT, repo_id TEXT,
    pre_version TEXT, post_version TEXT, content_version TEXT, version_status TEXT,
    response_hash TEXT,
    model_visible_chars INTEGER, model_visible_tokens INTEGER, token_attribution TEXT,
    token_estimator_id TEXT,
    success INTEGER, outcome TEXT, wall_time_ns INTEGER,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_te_stream ON tool_events(stream_key, seq);
CREATE INDEX IF NOT EXISTS idx_te_tuid   ON tool_events(tool_use_id);
-- persisted cross-delivery capture state (each hook delivery is its own process)
CREATE TABLE IF NOT EXISTS session_state (session_agent TEXT PRIMARY KEY, epoch INTEGER, step INTEGER);
CREATE TABLE IF NOT EXISTS pending_tools (tool_use_id TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS processed_batches (batch_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS capture_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hook_event TEXT, tool_use_id TEXT,
    exc_class TEXT, detail TEXT);
"""


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def default_hasher(path) -> Tuple[str, Optional[str]]:
    """Return (status, digest). Only `ok` and `absent` are version-COMPARABLE; directory/oversize/
    unavailable/hash_race are UNKNOWN-quality states with a NULL digest, so two failures can never
    compare equal and masquerade as a stable snapshot. Chunked + pre/post fstat so a file that
    changes during hashing is detected as a race, not a stable read."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return ("absent", "absent:v1")
    except OSError:
        return ("unavailable", None)
    if stat.S_ISDIR(st.st_mode):
        return ("directory", None)
    if st.st_size > MAX_HASH_BYTES:
        return ("oversize", None)
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        st2 = os.stat(path)
        if (st2.st_mtime_ns, st2.st_size) != (st.st_mtime_ns, st.st_size):
            return ("hash_race", None)
        return ("ok", "sha256:" + h.hexdigest())
    except OSError:
        return ("unavailable", None)


def _comparable(snap) -> Optional[str]:
    """The digest, only when the snapshot status is version-comparable (ok/absent); else None."""
    if not snap:
        return None
    status, digest = snap[0], (snap[1] if len(snap) > 1 else None)
    return digest if status in ("ok", "absent") else None


class HookJournal:
    def __init__(self, path="::memory::"):
        self.conn = sqlite3.connect(":memory:" if path in (":memory:", "::memory::") else str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        row = self.conn.execute("SELECT value FROM meta WHERE key='hook_schema_version'").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta VALUES ('hook_schema_version', ?)", (HOOK_SCHEMA_VERSION,))
            self.conn.commit()
        elif row["value"] != HOOK_SCHEMA_VERSION:
            raise RuntimeError(f"hook journal schema {row['value']} != {HOOK_SCHEMA_VERSION}; rebuild")

    # --- persisted capture state -------------------------------------------
    def session_state(self, sa: str) -> Tuple[int, int]:
        r = self.conn.execute("SELECT epoch, step FROM session_state WHERE session_agent=?", (sa,)).fetchone()
        return (r["epoch"], r["step"]) if r else (0, 0)

    def ensure_session(self, sa: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO session_state VALUES (?, 0, 0)", (sa,))

    def bump_epoch(self, sa: str) -> None:
        self.ensure_session(sa)
        self.conn.execute("UPDATE session_state SET epoch=epoch+1, step=0 WHERE session_agent=?", (sa,))

    def advance_step(self, sa: str) -> None:
        self.ensure_session(sa)
        self.conn.execute("UPDATE session_state SET step=step+1 WHERE session_agent=?", (sa,))

    def put_pending(self, tuid: str, payload: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO pending_tools VALUES (?, ?)", (tuid, json.dumps(payload)))

    def pop_pending(self, tuid: str) -> Optional[dict]:
        r = self.conn.execute("SELECT payload FROM pending_tools WHERE tool_use_id=?", (tuid,)).fetchone()
        if r is None:
            return None
        self.conn.execute("DELETE FROM pending_tools WHERE tool_use_id=?", (tuid,))
        return json.loads(r["payload"])

    def batch_seen(self, batch_id: str) -> bool:
        seen = self.conn.execute("SELECT 1 FROM processed_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if seen:
            return True
        self.conn.execute("INSERT OR IGNORE INTO processed_batches VALUES (?)", (batch_id,))
        return False

    def record_error(self, hook_event, tool_use_id, exc_class, detail) -> None:
        try:
            self.conn.execute(
                "INSERT INTO capture_errors(hook_event, tool_use_id, exc_class, detail) VALUES (?,?,?,?)",
                (hook_event, tool_use_id, exc_class, (detail or "")[:200]))
            self.conn.commit()
        except Exception:      # noqa: BLE001 -- if even the error log is unwritable, stay fail-open
            pass

    def capture_coverage(self) -> dict:
        got = self.conn.execute("SELECT COUNT(*) c FROM tool_events").fetchone()["c"]
        err = self.conn.execute("SELECT COUNT(*) c FROM capture_errors").fetchone()["c"]
        return {"events": got, "errors": err,
                "coverage": (got / (got + err)) if (got + err) else None}

    # --- tool events -------------------------------------------------------
    def put_tool_event(self, d: dict) -> None:
        cols = ",".join(d)
        ph = ",".join(f":{k}" for k in d)
        self.conn.execute(f"INSERT OR IGNORE INTO tool_events ({cols}) VALUES ({ph})", d)

    def stamp_batch(self, tool_use_ids, batch_id: str) -> None:
        n = len(tool_use_ids)
        q = ("UPDATE tool_events SET batch_id=?, batch_size=?, parallel=? WHERE tool_use_id IN (%s)"
             % ",".join("?" * n))
        self.conn.execute(q, (batch_id, n, int(n > 1), *tool_use_ids))

    def attribute_tokens(self, tool_use_id: str, total_tokens: int, chars: int) -> None:
        """Attach model-visible tokens ONCE per tool call. When a single tool_use produced several
        path materializations (e.g. `cat a b`), the one response can't be split, so mark those
        `ambiguous_multipath` and leave per-read tokens NULL rather than double-count."""
        rows = self.conn.execute(
            "SELECT event_id FROM tool_events WHERE tool_use_id=? AND kind='read'", (tool_use_id,)).fetchall()
        if len(rows) == 1:
            self.conn.execute(
                "UPDATE tool_events SET model_visible_tokens=?, model_visible_chars=?, "
                "token_attribution='attributed' WHERE event_id=?", (total_tokens, chars, rows[0]["event_id"]))
        elif len(rows) > 1:
            self.conn.execute(
                "UPDATE tool_events SET token_attribution='ambiguous_multipath' WHERE tool_use_id=? AND kind='read'",
                (tool_use_id,))

    def tool_events(self, stream_key: Optional[str] = None):
        if stream_key:
            return self.conn.execute(
                "SELECT * FROM tool_events WHERE stream_key=? ORDER BY seq", (stream_key,)).fetchall()
        return self.conn.execute("SELECT * FROM tool_events ORDER BY seq").fetchall()

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


_READ_TOOLS = {"Read"}
_EDIT_TOOLS = {"Edit": "native_edit", "Write": "native_write",
               "MultiEdit": "native_edit", "NotebookEdit": "native_edit"}


def _norm_path(path: Optional[str], cwd: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(absolute-lexical, normalized-identity). Resolve a relative path against the event's cwd so a
    Bash `src/a.py` and a native `/repo/src/a.py` share one identity for classification. Lexical
    only (no realpath) -- symlink spelling can itself matter."""
    if not path:
        return (None, None)
    absolute = path if os.path.isabs(path) else (os.path.join(cwd, path) if cwd else path)
    return (absolute, os.path.normpath(absolute))


class HookCapture:
    """Stateless across deliveries: all cross-event state lives in the HookJournal, so a PreToolUse
    in one process and its PostToolUse in another still finalize correctly."""

    def __init__(self, journal: HookJournal, hasher: Optional[Callable[[str], Tuple[str, Optional[str]]]] = None):
        self.j = journal
        self.hash = hasher or default_hasher

    def on_event(self, ev: dict) -> None:
        try:
            self._dispatch(ev)
            self.j.commit()
        except Exception as e:            # noqa: BLE001 -- fail-open, but LOG the gap (not invisible)
            self.j.record_error(ev.get("hook_event_name"), ev.get("tool_use_id"), type(e).__name__, str(e))

    def _sa(self, ev) -> str:
        return f"{ev.get('session_id')}:{ev.get('agent_id') or 'main'}"

    def _stream(self, ev) -> str:
        epoch, _ = self.j.session_state(self._sa(ev))
        return f"{self._sa(ev)}:{epoch}"

    def _dispatch(self, ev):
        e = ev.get("hook_event_name")
        if e == "SessionStart":
            if ev.get("source") == "clear":
                self.j.bump_epoch(self._sa(ev))       # /clear = new lineage; resume/compact keep it
            else:
                self.j.ensure_session(self._sa(ev))
        elif e == "SubagentStart":
            self.j.ensure_session(self._sa(ev))
        elif e == "UserPromptSubmit":
            self.j.advance_step(self._sa(ev))         # model-request epoch += 1
        elif e == "PreToolUse":
            self._pre(ev)
        elif e in ("PostToolUse", "PostToolUseFailure"):
            self._post(ev, success=(e == "PostToolUse"))
        elif e == "PostToolBatch":
            self._batch(ev)

    def _effects(self, tool, tinput, cwd):
        out = []
        if tool in _READ_TOOLS:
            out.append({"kind": "read", "channel": NATIVE_READ, "mutation_source": None,
                        "representation": "file", "raw_path": tinput.get("file_path"), "ref": None})
        elif tool in _EDIT_TOOLS:
            out.append({"kind": "edit", "channel": "edit", "mutation_source": _EDIT_TOOLS[tool],
                        "representation": "file", "raw_path": tinput.get("file_path"), "ref": None})
        elif tool == "Bash":
            for x in bash_effects(tinput.get("command", "")):
                if x.kind == "read":
                    out.append({"kind": "read", "channel": BASH_MATERIALIZATION, "mutation_source": None,
                                "representation": x.representation, "raw_path": x.path, "ref": x.ref})
                elif x.kind == "edit":
                    out.append({"kind": "edit", "channel": "edit", "mutation_source": "bash",
                                "representation": "file", "raw_path": x.path, "ref": None})
        for x in out:
            x["path_abs"], x["path_norm"] = _norm_path(x["raw_path"], cwd)
        return out

    def _pre(self, ev):
        effects = self._effects(ev.get("tool_name"), ev.get("tool_input") or {}, ev.get("cwd"))
        for x in effects:
            if x["path_abs"] and x["representation"] == "file":
                x["pre"] = list(self.hash(x["path_abs"]))     # (status, digest) -> list for JSON
        self.j.put_pending(ev.get("tool_use_id"), {
            "effects": effects, "step": self.j.session_state(self._sa(ev))[1], "stream": self._stream(ev),
            "tool_name": ev.get("tool_name"), "prompt_id": ev.get("prompt_id"),
            "session_id": ev.get("session_id"), "agent_id": ev.get("agent_id"), "cwd": ev.get("cwd")})

    def _post(self, ev, success):
        p = self.j.pop_pending(ev.get("tool_use_id"))
        if p is None:
            return
        resp = ev.get("tool_response")
        resp_hash = _sha(resp.encode("utf-8", "replace")) if isinstance(resp, str) else None
        for ordinal, x in enumerate(p["effects"]):
            path_norm = x.get("path_norm")
            if not path_norm:
                continue
            pre = _comparable(x.get("pre"))
            if x["representation"] == "git_blob":
                content_version, version_status, post = resp_hash, "stable", None
            else:
                post_snap = self.hash(x["path_abs"])
                post = _comparable(post_snap)
                if x["kind"] == "read":
                    if pre is None or post is None:
                        content_version, version_status = None, (x.get("pre") or [None])[0] or post_snap[0]
                    elif pre == post:
                        content_version, version_status = pre, "stable"
                    else:
                        content_version, version_status = None, "raced"
                else:                                          # edit
                    if pre is not None and post is not None and pre == post:
                        continue                               # identical bytes -> not a mutation
                    content_version, version_status = pre, ("stable" if (pre and post) else "unverified")
            outcome = "success" if success else ("failed_partial" if x["kind"] == "edit" else "failed")
            eid = f"{ev.get('tool_use_id')}:{x['kind']}:{ordinal}:{hashlib.sha1(path_norm.encode()).hexdigest()[:8]}"
            self.j.put_tool_event({
                "event_id": eid, "session_id": p["session_id"], "agent_id": p["agent_id"],
                "stream_key": p["stream"], "prompt_id": p["prompt_id"], "cwd": p["cwd"], "step": p["step"],
                "batch_id": None, "batch_size": None, "parallel": None,
                "tool_use_id": ev.get("tool_use_id"), "tool_name": p["tool_name"], "kind": x["kind"],
                "channel": x["channel"], "mutation_source": x["mutation_source"],
                "representation": x["representation"],
                "path_absolute": x.get("path_abs"), "path_normalized": path_norm,
                "repo_relative": None, "repo_id": None,
                "pre_version": pre, "post_version": post, "content_version": content_version,
                "version_status": version_status, "response_hash": resp_hash,
                "model_visible_chars": None, "model_visible_tokens": None, "token_attribution": None,
                "token_estimator_id": "chars4-v1", "success": int(success), "outcome": outcome,
                "wall_time_ns": ev.get("wall_time_ns"), "schema_version": HOOK_SCHEMA_VERSION})

    def _batch(self, ev):
        calls = ev.get("tool_calls") or []
        tuids = [c.get("tool_use_id") for c in calls if c.get("tool_use_id")]
        bid = "b_" + hashlib.sha1(
            (self._stream(ev) + str(ev.get("prompt_id")) + "|".join(sorted(tuids))).encode()).hexdigest()[:12]
        if self.j.batch_seen(bid):
            return                                             # idempotent: a re-delivered batch is a no-op
        if tuids:
            self.j.stamp_batch(tuids, bid)
        for c in calls:                                        # PostToolBatch response = model-visible content
            resp = c.get("tool_response")
            if isinstance(resp, str) and c.get("tool_use_id"):
                self.j.attribute_tokens(c["tool_use_id"], est_tokens(resp), len(resp))
        self.j.advance_step(self._sa(ev))                      # model-request epoch advances after the batch
