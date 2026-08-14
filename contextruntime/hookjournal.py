"""HookJournal -- prospective observation layer (Phase 2.4-C).

A SEPARATE SQLite store (hook_schema 0.1.0), NOT the frozen B.1 GraphStore, so live hook capture
never touches the frozen telemetry contract and the journal can be replayed through different
normalizers/windows without recapturing. Captures are METADATA-ONLY (hashes + counts) -- never
raw file/edit/command contents -- so the journal is privacy-safe to keep and share.

HookCapture turns Claude Code hook deliveries (SessionStart/SubagentStart, UserPromptSubmit,
PreToolUse, PostToolUse/PostToolUseFailure, PostToolBatch) into consolidated per-path tool events:
  - agent-step = MODEL-REQUEST EPOCH per stream: +1 on UserPromptSubmit and on PostToolBatch;
    every PreToolUse is stamped with the current step.
  - TWO-SNAPSHOT hashing: pre-hash at PreToolUse, post-hash at Post. A read with pre==post is
    `stable` (content_version = that hash); pre!=post is a `raced` read (content_version unknown).
  - A mutation is an OBSERVED STATE TRANSITION (pre != post), not merely a successful Edit tool:
    an identical-bytes write is not a mutation, and a failed op that changed bytes still is.
  - Bash is split into per-path effects via normalize.bash_effects (conservative recognizer).
  - FAIL-OPEN: any capture error is swallowed so a tool call is never blocked.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from .ingest import est_tokens
from .normalize import BASH_MATERIALIZATION, NATIVE_READ, bash_effects

HOOK_SCHEMA_VERSION = "0.1.0"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    session_id TEXT, agent_id TEXT, stream_key TEXT, prompt_id TEXT,
    step INTEGER,
    batch_id TEXT, batch_size INTEGER, parallel INTEGER,
    tool_use_id TEXT, tool_name TEXT,
    kind TEXT, channel TEXT, mutation_source TEXT, representation TEXT,
    path_absolute TEXT, path_normalized TEXT, repo_relative TEXT, repo_id TEXT,
    pre_version TEXT, post_version TEXT, content_version TEXT, version_status TEXT,
    input_hash TEXT, response_hash TEXT,
    model_visible_chars INTEGER, estimated_tokens INTEGER, token_estimator_id TEXT,
    success INTEGER, outcome TEXT, wall_time_ns INTEGER,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_te_stream ON tool_events(stream_key, seq);
CREATE INDEX IF NOT EXISTS idx_te_tuid   ON tool_events(tool_use_id);
"""


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def default_hasher(path) -> str:
    """Full-file digest; a missing file is a KNOWN state (`absent:v1`), not an unknown measurement."""
    try:
        return _sha(Path(path).read_bytes())
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return "absent:v1"
    except OSError:
        return "unavailable"


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

    def put_tool_event(self, d: dict) -> None:
        cols = ",".join(d)
        ph = ",".join(f":{k}" for k in d)
        self.conn.execute(f"INSERT OR IGNORE INTO tool_events ({cols}) VALUES ({ph})", d)

    def stamp_batch(self, tool_use_ids, batch_id: str) -> None:
        q = ("UPDATE tool_events SET batch_id=?, batch_size=?, parallel=? WHERE tool_use_id IN (%s)"
             % ",".join("?" * len(tool_use_ids)))
        n = len(tool_use_ids)
        self.conn.execute(q, (batch_id, n, int(n > 1), *tool_use_ids))

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


class HookCapture:
    def __init__(self, journal: HookJournal, hasher: Optional[Callable[[str], str]] = None):
        self.j = journal
        self.hash = hasher or default_hasher
        self.step: dict = {}          # stream_key -> model-request epoch
        self.pending: dict = {}       # tool_use_id -> captured PreToolUse info

    @staticmethod
    def _stream(ev) -> str:
        return f"{ev.get('session_id')}:{ev.get('agent_id') or 'main'}"

    def on_event(self, ev: dict) -> None:
        try:
            self._dispatch(ev)
        except Exception:             # noqa: BLE001 -- fail-open: never block a tool call
            pass

    def _dispatch(self, ev):
        e, s = ev.get("event"), self._stream(ev)
        if e in ("SessionStart", "SubagentStart"):
            self.step.setdefault(s, 0)
        elif e == "UserPromptSubmit":
            self.step[s] = self.step.get(s, 0) + 1
        elif e == "PreToolUse":
            self._pre(ev, s)
        elif e in ("PostToolUse", "PostToolUseFailure"):
            self._post(ev, s, success=(e == "PostToolUse"))
        elif e == "PostToolBatch":
            self._batch(ev, s)

    def _effects(self, tool, tinput):
        if tool in _READ_TOOLS:
            return [{"kind": "read", "path": tinput.get("file_path"), "representation": "file",
                     "channel": NATIVE_READ, "mutation_source": None, "ref": None}]
        if tool in _EDIT_TOOLS:
            return [{"kind": "edit", "path": tinput.get("file_path"), "representation": "file",
                     "channel": "edit", "mutation_source": _EDIT_TOOLS[tool], "ref": None}]
        if tool == "Bash":
            out = []
            for x in bash_effects(tinput.get("command", "")):
                if x.kind == "read":
                    out.append({"kind": "read", "path": x.path, "representation": x.representation,
                                "channel": BASH_MATERIALIZATION, "mutation_source": None, "ref": x.ref})
                elif x.kind == "edit":
                    out.append({"kind": "edit", "path": x.path, "representation": "file",
                                "channel": "edit", "mutation_source": "bash", "ref": None})
            return out
        return []

    def _pre(self, ev, s):
        effects = self._effects(ev.get("tool_name"), ev.get("tool_input") or {})
        for x in effects:
            if x["path"] and x["representation"] == "file":
                x["pre_version"] = self.hash(x["path"])       # synchronous pre-state snapshot
        self.pending[ev.get("tool_use_id")] = {
            "effects": effects, "step": self.step.get(s, 1), "stream": s,
            "tool_name": ev.get("tool_name"), "prompt_id": ev.get("prompt_id"),
            "session_id": ev.get("session_id"), "agent_id": ev.get("agent_id")}

    def _post(self, ev, s, success):
        p = self.pending.pop(ev.get("tool_use_id"), None)
        if p is None:
            return
        resp = ev.get("tool_response") or ""
        resp_hash = _sha(resp.encode("utf-8", "replace")) if resp else None
        est = est_tokens(resp) if resp else None
        for ordinal, x in enumerate(p["effects"]):
            path = x["path"]
            if not path:
                continue
            if x["representation"] == "git_blob":
                content_version, version_status, pre_v, post_v = resp_hash, "stable", None, None
            else:
                pre_v = x.get("pre_version")
                post_v = self.hash(path)                       # synchronous post-state snapshot
                if x["kind"] == "read":
                    content_version, version_status = (pre_v, "stable") if pre_v == post_v else (None, "raced")
                else:
                    if pre_v == post_v:
                        continue                              # identical write -> not a mutation
                    content_version, version_status = pre_v, "stable"   # edit's PRE-version
            outcome = "success" if success else ("failed_partial" if x["kind"] == "edit" else "failed")
            eid = f"{ev.get('tool_use_id')}:{x['kind']}:{ordinal}:{hashlib.sha1((path).encode()).hexdigest()[:8]}"
            self.j.put_tool_event({
                "event_id": eid, "session_id": p["session_id"], "agent_id": p["agent_id"],
                "stream_key": p["stream"], "prompt_id": p["prompt_id"], "step": p["step"],
                "batch_id": None, "batch_size": None, "parallel": None,
                "tool_use_id": ev.get("tool_use_id"), "tool_name": p["tool_name"], "kind": x["kind"],
                "channel": x["channel"], "mutation_source": x["mutation_source"],
                "representation": x["representation"],
                "path_absolute": path, "path_normalized": path, "repo_relative": None, "repo_id": None,
                "pre_version": pre_v, "post_version": post_v, "content_version": content_version,
                "version_status": version_status, "input_hash": None, "response_hash": resp_hash,
                "model_visible_chars": (len(resp) if resp else None), "estimated_tokens": est,
                "token_estimator_id": "chars4-v1", "success": int(success), "outcome": outcome,
                "wall_time_ns": ev.get("wall_time_ns"), "schema_version": HOOK_SCHEMA_VERSION})
        self.j.commit()

    def _batch(self, ev, s):
        tuids = ev.get("tool_use_ids") or []
        if tuids:
            bid = hashlib.sha1((s + str(ev.get("prompt_id")) + "|".join(sorted(tuids))).encode()).hexdigest()[:12]
            self.j.stamp_batch(tuids, "b_" + bid)
        self.step[s] = self.step.get(s, 0) + 1                 # model-request epoch advances after the batch
        self.j.commit()
