"""HookJournal -- prospective observation layer (Phase 2.4-C), wired to the REAL Claude Code hook
contract with evidence-integrity guarantees at the "observed vs entitled-to-claim" boundary.
A SEPARATE SQLite store (hook_schema 0.3.0), never the frozen B.1 GraphStore.

Integrity guarantees (why each matters for the eventual percentages):
  - ATOMIC delivery: each hook delivery runs inside a SAVEPOINT; on any error it ROLLS BACK, so a
    transient instrumentation failure can never commit half a delivery (deleted pending, claimed
    batch) and destroy replayability. The error is then logged in a fresh transaction.
  - MUTATION CERTAINTY: a mutation is `verified_change` (pre != post, both hashed), `verified_noop`
    (equal -> not recorded), or `unverified` (a hash was unavailable). An unverified mutation is an
    UNCERTAINTY boundary: it can never produce an EDIT_PRECONDITION.
  - TRUTHFUL COVERAGE: SPLIT ledgers -- pre_tool_calls_seen vs batch_tool_calls_resolved (are we
    missing PreToolUse deliveries?) and, for Bash, a THREE-way split (0.4.0): bash_materialization_calls
    (produced a read/edit) vs execution_bash_calls (tests/python -- recognized, not a source read) vs
    unknown_bash_calls (truly unrecognized). bash_unknown_share = unknown/bash therefore reflects missed
    SOURCE context, not test-running noise -- execution is not counted as blindness.
  - MODEL-VISIBLE RESPONSE: measured from PostToolBatch (string OR text content-block array), with
    multimodal/unsupported marked -- never silently unmeasured. That PostToolBatch payload is also
    the AUTHORITATIVE response_hash (attribute_tokens stamps it), not PostToolUse's structured
    tool_response. Counts are ESTIMATED tokens.
  - GIT BLOB versions resolved at capture via `git cat-file blob ref:path` and SHA-256 of the raw
    bytes (same namespace as worktree digests), so a `git show` read conflicts correctly.
  - HASHING opens the fd once and fstat's before/after (device+inode+size+mtime), so an atomic path
    replacement mid-hash is a race, not a false stable. wall-time comes from an INJECTED clock
    (Claude's payload has no wall_time field).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from typing import Callable, Optional, Tuple

from .ingest import est_tokens
from .normalize import BASH_MATERIALIZATION, NATIVE_READ, bash_effects

HOOK_SCHEMA_VERSION = "0.4.0"   # 0.4.0: representation-typed shell (search/path materialization + execution)
MAX_HASH_BYTES = 32 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    session_id TEXT, agent_id TEXT, stream_key TEXT, prompt_id TEXT, cwd TEXT,
    step INTEGER,
    batch_id TEXT, batch_size INTEGER, parallel INTEGER,
    tool_use_id TEXT, tool_name TEXT,
    kind TEXT, channel TEXT, mutation_source TEXT, mutation_status TEXT, representation TEXT,
    path_absolute TEXT, path_normalized TEXT, repo_relative TEXT, repo_id TEXT,
    pre_version TEXT, post_version TEXT, content_version TEXT, version_status TEXT,
    response_hash TEXT,
    model_visible_chars INTEGER, model_visible_tokens INTEGER, token_status TEXT, token_attribution TEXT,
    token_estimator_id TEXT,
    success INTEGER, outcome TEXT, wall_time_ns INTEGER,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_te_stream ON tool_events(stream_key, seq);
CREATE INDEX IF NOT EXISTS idx_te_tuid   ON tool_events(tool_use_id);
CREATE TABLE IF NOT EXISTS session_state (session_agent TEXT PRIMARY KEY, epoch INTEGER, step INTEGER);
CREATE TABLE IF NOT EXISTS pending_tools (tool_use_id TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS processed_batches (batch_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS capture_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, hook_event TEXT, tool_use_id TEXT, exc_class TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS capture_stats (metric TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
"""


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def default_hasher(path) -> Tuple[str, Optional[str]]:
    """(status, digest). Opens the fd ONCE and fstat's before/after (device+inode+size+mtime); a
    file replaced/changed mid-hash is a `hash_race`, not a false `stable`. Only ok/absent are
    version-comparable; directory/oversize/unavailable/hash_race carry a NULL digest."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return ("absent", "absent:v1")
    except OSError:
        return ("unavailable", None)
    try:
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            return ("directory", None)
        if st.st_size > MAX_HASH_BYTES:
            return ("oversize", None)
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            h.update(chunk)
        st2 = os.fstat(fd)
        ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if (st2.st_dev, st2.st_ino, st2.st_size, st2.st_mtime_ns) != ident:
            return ("hash_race", None)
        return ("ok", "sha256:" + h.hexdigest())
    except OSError:
        return ("unavailable", None)
    finally:
        os.close(fd)


def default_git_blob_hasher(cwd, ref, path) -> Tuple[str, Optional[str], Optional[str]]:
    """(status, digest, canonical_path). SHA-256 the raw bytes of `ref:path` (same namespace as a
    worktree file digest), and resolve the CANONICAL worktree path -- a git path is repository-tree-
    relative, so a `git show HEAD:src/a.py` from `/repo/sub` must join a native edit of
    `/repo/src/a.py`, not `/repo/sub/src/a.py`."""
    try:
        blob = subprocess.run(["git", "-C", cwd or ".", "cat-file", "blob", f"{ref}:{path}"],
                              capture_output=True, timeout=5)
        top = subprocess.run(["git", "-C", cwd or ".", "rev-parse", "--show-toplevel"],
                             capture_output=True, timeout=5)
    except Exception:      # noqa: BLE001 -- git absent / timeout / bad ref
        return ("unavailable", None, None)
    if blob.returncode != 0:
        return ("unavailable", None, None)
    canonical = (os.path.normpath(os.path.join(top.stdout.decode("utf-8", "replace").strip(), path))
                 if top.returncode == 0 else None)
    return ("ok", _sha(blob.stdout), canonical)


def measure_model_visible_response(resp) -> dict:
    """Model-visible chars/estimated tokens/hash from a PostToolBatch response, which may be a
    serialized string OR a text content-block array. Multimodal/unsupported is marked, not silently
    dropped. Tokens are ESTIMATED (chars/4) until a provider tokenizer is used."""
    text, status = None, "unsupported"
    if isinstance(resp, str):
        text, status = resp, "text"
    elif isinstance(resp, list):
        blocks = [b for b in resp if isinstance(b, dict)]
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        non_text = any(b.get("type") != "text" for b in blocks)
        if texts:
            text = "".join(texts)
            status = "text_partial_multimodal" if non_text else "text"
        elif non_text:
            status = "multimodal"
    if text is None:
        return {"chars": None, "tokens": None, "hash": None, "status": status}
    return {"chars": len(text), "tokens": est_tokens(text), "hash": _sha(text.encode("utf-8", "replace")),
            "status": status}


class HookJournal:
    def __init__(self, path="::memory::"):
        import sqlite3
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
        self.conn.execute("UPDATE session_state SET step=step+1 WHERE session_agent=?", (sa,))   # atomic increment

    def put_pending(self, tuid: str, payload: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO pending_tools VALUES (?, ?)", (tuid, json.dumps(payload)))

    def pop_pending(self, tuid: str) -> Optional[dict]:
        r = self.conn.execute("SELECT payload FROM pending_tools WHERE tool_use_id=?", (tuid,)).fetchone()
        if r is None:
            return None
        self.conn.execute("DELETE FROM pending_tools WHERE tool_use_id=?", (tuid,))
        return json.loads(r["payload"])

    def claim_batch(self, batch_id: str) -> bool:
        """Atomic claim: the INSERT itself is the claim, so two concurrent replays can't both
        proceed. Returns True only for the first caller (rowcount == 1)."""
        cur = self.conn.execute("INSERT OR IGNORE INTO processed_batches VALUES (?)", (batch_id,))
        return cur.rowcount == 1

    def bump(self, metric: str, n: int = 1) -> None:
        self.conn.execute(
            "INSERT INTO capture_stats(metric, n) VALUES (?, ?) "
            "ON CONFLICT(metric) DO UPDATE SET n = n + ?", (metric, n, n))

    def record_error(self, hook_event, tool_use_id, exc_class, detail) -> None:
        try:
            self.conn.execute(
                "INSERT INTO capture_errors(hook_event, tool_use_id, exc_class, detail) VALUES (?,?,?,?)",
                (hook_event, tool_use_id, exc_class, (detail or "")[:200]))
            self.conn.commit()
        except Exception:      # noqa: BLE001
            pass

    def capture_stats(self) -> dict:
        d = {r["metric"]: r["n"] for r in self.conn.execute("SELECT metric, n FROM capture_stats")}
        errs = self.conn.execute("SELECT COUNT(*) c FROM capture_errors").fetchone()["c"]
        deliveries = d.get("deliveries", 0)
        pre = d.get("pre_tool_calls_seen", 0)
        resolved = d.get("batch_tool_calls_resolved", 0)
        bash = d.get("bash_calls_seen", 0)
        d["errors"] = errs
        d["delivery_success_ratio"] = ((deliveries - errs) / deliveries) if deliveries else None
        # PreToolUse deliveries seen vs the batch's authoritative resolved count (are we missing any?)
        d["pre_capture_rate"] = (pre / resolved) if resolved else None
        # Bash blindness measured against BASH calls only, not diluted by every other tool
        d["bash_unknown_share"] = (d.get("unknown_bash_calls", 0) / bash) if bash else None
        return d

    # --- tool events -------------------------------------------------------
    def put_tool_event(self, dd: dict) -> None:
        cols = ",".join(dd)
        ph = ",".join(f":{k}" for k in dd)
        self.conn.execute(f"INSERT OR IGNORE INTO tool_events ({cols}) VALUES ({ph})", dd)

    def stamp_batch(self, tool_use_ids, batch_id: str) -> None:
        n = len(tool_use_ids)
        q = ("UPDATE tool_events SET batch_id=?, batch_size=?, parallel=? WHERE tool_use_id IN (%s)"
             % ",".join("?" * n))
        self.conn.execute(q, (batch_id, n, int(n > 1), *tool_use_ids))

    def attribute_tokens(self, tool_use_id: str, tokens, chars, token_status, response_hash=None) -> None:
        rows = self.conn.execute(
            "SELECT event_id FROM tool_events WHERE tool_use_id=? AND kind='read'", (tool_use_id,)).fetchall()
        # The AUTHORITATIVE model-visible response hash lives on PostToolBatch (the model-visible
        # payload), not on PostToolUse's structured tool_response -- so we stamp it here.
        if len(rows) == 1:
            self.conn.execute(
                "UPDATE tool_events SET model_visible_tokens=?, model_visible_chars=?, token_status=?, "
                "response_hash=?, token_attribution='attributed' WHERE event_id=?",
                (tokens, chars, token_status, response_hash, rows[0]["event_id"]))
        elif len(rows) > 1:
            # one response, many materialization paths -> tokens AND response_hash are ambiguous
            self.conn.execute(
                "UPDATE tool_events SET token_status=?, token_attribution='ambiguous_multipath' "
                "WHERE tool_use_id=? AND kind='read'", (token_status, tool_use_id))

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


def _norm_path(path, cwd):
    if not path:
        return (None, None)
    absolute = path if os.path.isabs(path) else (os.path.join(cwd, path) if cwd else path)
    return (absolute, os.path.normpath(absolute))


class HookCapture:
    def __init__(self, journal, hasher=None, git_blob_hasher=None, clock=None):
        self.j = journal
        self.hash = hasher or default_hasher
        self.git = git_blob_hasher or default_git_blob_hasher
        self.clock = clock or time.time_ns

    def on_event(self, ev: dict) -> None:
        # FAIL-OPEN: absolutely nothing -- not the ledger bump, not the SAVEPOINT itself, not the
        # dispatch -- is allowed to escape and block the tool call. The deliveries bump is taken
        # BEFORE the savepoint so a rolled-back delivery is still counted (else the failure would be
        # invisible to delivery_success_ratio).
        conn = self.j.conn
        try:
            self.j.bump("deliveries")
            conn.execute("SAVEPOINT cr_hook")
            self._dispatch(ev)
            conn.execute("RELEASE cr_hook")
            self.j.commit()
        except Exception as e:            # noqa: BLE001 -- atomic rollback, logged (not invisible)
            try:
                conn.execute("ROLLBACK TO cr_hook")
                conn.execute("RELEASE cr_hook")
            except Exception:             # noqa: BLE001 -- savepoint may never have been created
                pass
            self.j.record_error(ev.get("hook_event_name"), ev.get("tool_use_id"), type(e).__name__, str(e))

    def _sa(self, ev) -> str:
        return f"{ev.get('session_id')}:{ev.get('agent_id') or 'main'}"

    def _stream(self, ev) -> str:
        return f"{self._sa(ev)}:{self.j.session_state(self._sa(ev))[0]}"

    def _dispatch(self, ev):
        e = ev.get("hook_event_name")
        if e == "SessionStart":
            (self.j.bump_epoch if ev.get("source") == "clear" else self.j.ensure_session)(self._sa(ev))
        elif e == "SubagentStart":
            self.j.ensure_session(self._sa(ev))
        elif e == "UserPromptSubmit":
            self.j.advance_step(self._sa(ev))
        elif e == "PreToolUse":
            self._pre(ev)
        elif e in ("PostToolUse", "PostToolUseFailure"):
            self._post(ev, success=(e == "PostToolUse"))
        elif e == "PostToolBatch":
            self._batch(ev)

    def _effects(self, tool, tinput, cwd):
        out, bash_kinds = [], []
        if tool in _READ_TOOLS:
            out.append({"kind": "read", "channel": NATIVE_READ, "mutation_source": None,
                        "representation": "file", "raw_path": tinput.get("file_path"), "ref": None})
        elif tool in _EDIT_TOOLS:
            out.append({"kind": "edit", "channel": "edit", "mutation_source": _EDIT_TOOLS[tool],
                        "representation": "file", "raw_path": tinput.get("file_path"), "ref": None})
        elif tool == "Bash":
            effs = bash_effects(tinput.get("command", ""))
            bash_kinds = [x.kind for x in effs]
            for x in effs:
                if x.kind == "read":                             # file / git_blob / search / path_listing
                    out.append({"kind": "read", "channel": BASH_MATERIALIZATION, "mutation_source": None,
                                "representation": x.representation, "raw_path": x.path, "ref": x.ref})
                elif x.kind == "edit":
                    out.append({"kind": "edit", "channel": "edit", "mutation_source": "bash",
                                "representation": "file", "raw_path": x.path, "ref": None})
                # "execution" and "unknown" produce no tool_event -- they are counted in the ledger only
        for x in out:
            x["path_abs"], x["path_norm"] = _norm_path(x["raw_path"], cwd)
        return out, bash_kinds

    def _pre(self, ev):
        cwd = ev.get("cwd")
        tool = ev.get("tool_name")
        effects, bash_kinds = self._effects(tool, ev.get("tool_input") or {}, cwd)
        # Split ledgers: PreToolUse deliveries vs BASH classification. A Bash call is materialization
        # (produced a read/edit), execution (tests/python -- recognized but not a source read), or
        # unknown (truly unrecognized). unknown EXCLUDES execution, so bash_unknown_share reflects
        # missed SOURCE context, not test-running noise.
        self.j.bump("pre_tool_calls_seen")
        if tool == "Bash":
            self.j.bump("bash_calls_seen")
            if any(k in ("read", "edit") for k in bash_kinds):
                self.j.bump("bash_materialization_calls")
            elif "execution" in bash_kinds:
                self.j.bump("execution_bash_calls")
            else:
                self.j.bump("unknown_bash_calls")
        for x in effects:
            if x["representation"] == "git_blob" and x["ref"] and x["raw_path"]:
                status, digest, canonical = self.git(cwd, x["ref"], x["raw_path"])   # resolve blob at capture
                x["pre"] = [status, digest]
                if canonical:                                # git path is repo-tree-relative, not cwd-relative
                    x["path_abs"] = x["path_norm"] = canonical
            elif x["path_abs"] and x["representation"] == "file":
                x["pre"] = list(self.hash(x["path_abs"]))
        self.j.put_pending(ev.get("tool_use_id"), {
            "effects": effects, "step": self.j.session_state(self._sa(ev))[1], "stream": self._stream(ev),
            "tool_name": ev.get("tool_name"), "prompt_id": ev.get("prompt_id"),
            "session_id": ev.get("session_id"), "agent_id": ev.get("agent_id"), "cwd": cwd,
            "wall_time_ns": self.clock()})

    @staticmethod
    def _cmp(snap):
        if not snap:
            return None
        status = snap[0]
        digest = snap[1] if len(snap) > 1 else None
        return digest if status in ("ok", "absent") else None

    def _post(self, ev, success):
        p = self.j.pop_pending(ev.get("tool_use_id"))
        if p is None:
            return
        # response_hash is assigned authoritatively at PostToolBatch (model-visible payload), NOT from
        # this structured tool_response -- so we leave it null here and let attribute_tokens stamp it.
        for ordinal, x in enumerate(p["effects"]):
            path_norm = x.get("path_norm")
            if not path_norm:
                continue
            pre = self._cmp(x.get("pre"))
            mutation_status, post = None, None
            if x["representation"] == "git_blob":
                content_version = pre                        # resolved at capture from the blob bytes
                version_status = "stable" if pre is not None else (x.get("pre") or ["unavailable"])[0]
            else:
                post = self._cmp(self.hash(x["path_abs"]))
                if x["kind"] == "read":
                    if pre is None or post is None:
                        content_version, version_status = None, "unverified"
                    elif pre == post:
                        content_version, version_status = pre, "stable"
                    else:
                        content_version, version_status = None, "raced"
                else:                                        # edit -> mutation certainty
                    if pre is not None and post is not None:
                        if pre == post:
                            continue                         # verified_noop -> not a mutation
                        content_version, version_status, mutation_status = pre, "stable", "verified_change"
                    else:
                        content_version, version_status, mutation_status = pre, "unverified", "unverified"
            if x["kind"] == "edit":
                outcome = ("failed_partial" if (not success and mutation_status == "verified_change")
                           else ("failed_uncertain" if not success else "success"))
            else:
                outcome = "success" if success else "failed"
            eid = f"{ev.get('tool_use_id')}:{x['kind']}:{ordinal}:{hashlib.sha1(path_norm.encode()).hexdigest()[:8]}"
            self.j.put_tool_event({
                "event_id": eid, "session_id": p["session_id"], "agent_id": p["agent_id"],
                "stream_key": p["stream"], "prompt_id": p["prompt_id"], "cwd": p["cwd"], "step": p["step"],
                "batch_id": None, "batch_size": None, "parallel": None,
                "tool_use_id": ev.get("tool_use_id"), "tool_name": p["tool_name"], "kind": x["kind"],
                "channel": x["channel"], "mutation_source": x["mutation_source"],
                "mutation_status": mutation_status, "representation": x["representation"],
                "path_absolute": x.get("path_abs"), "path_normalized": path_norm,
                "repo_relative": None, "repo_id": None,
                "pre_version": pre, "post_version": post, "content_version": content_version,
                "version_status": version_status, "response_hash": None,
                "model_visible_chars": None, "model_visible_tokens": None, "token_status": None,
                "token_attribution": None, "token_estimator_id": "chars4-v1",
                "success": int(success), "outcome": outcome, "wall_time_ns": p.get("wall_time_ns"),
                "schema_version": HOOK_SCHEMA_VERSION})

    def _batch(self, ev):
        calls = ev.get("tool_calls") or []
        tuids = [c.get("tool_use_id") for c in calls if isinstance(c, dict) and c.get("tool_use_id")]
        bid = "b_" + hashlib.sha1(
            (self._stream(ev) + str(ev.get("prompt_id")) + "|".join(sorted(tuids))).encode()).hexdigest()[:12]
        if not self.j.claim_batch(bid):
            return                                           # already processed -> idempotent no-op
        if tuids:
            self.j.stamp_batch(tuids, bid)
        # the batch is the AUTHORITATIVE count of tool calls in this step -- pre_capture_rate compares
        # PreToolUse deliveries seen against it, so a dropped PreToolUse shows up as < 1.0.
        self.j.bump("batch_tool_calls_resolved", len(tuids))
        for c in calls:                                      # PostToolBatch response = model-visible content
            if isinstance(c, dict) and c.get("tool_use_id"):
                rm = measure_model_visible_response(c.get("tool_response"))
                self.j.attribute_tokens(c["tool_use_id"], rm["tokens"], rm["chars"], rm["status"], rm["hash"])
        self.j.advance_step(self._sa(ev))
