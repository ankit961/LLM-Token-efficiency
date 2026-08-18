"""B2.2 — edit-safety eligibility for file-read residency reduction.

A file read is only compacted if it is safe: the agent has NOT already mutated that file this
session (a mutated file is an active edit target — keep its exact content resident so the next Edit
matches). Files edited LATER are still compacted at read time (we can't see the future), and the
compact-until-edit residency model relies on the agent re-Reading — which materializes exact content
— before it edits a compacted region. Correctness is underwritten two ways: (a) the exact raw is
always in the CAS (result:// recovery), and (b) the Edit tool matches its old_string against the
file ON DISK, so a compacted read can never produce a WRONG edit — at worst a forced re-read.

This module is pure/read-only and fail-open: any doubt ⇒ NOT eligible ⇒ the file passes through.
It is NOT wired into the frozen B1 hook; B2 stays behind CR_REDUCE_FILES until B2.4.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

_FILE_READ_TOOLS = frozenset({"Read", "NotebookRead"})


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").strip().rstrip("/")


def edited_paths_from_journal(journal_db: Optional[str], session_id: Optional[str],
                              limit: int = 1000) -> frozenset:
    """Paths the session has MUTATED (any non-read tool event with a path) — the files that must stay
    resident in full. Read-only sqlite, swallow every error (a missing/locked journal ⇒ empty set ⇒
    nothing spared on that basis; the beneficial + exact-recovery guards still protect correctness)."""
    if not journal_db or not session_id:
        return frozenset()
    try:
        conn = sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT path_normalized FROM tool_events "
                "WHERE session_id=? AND kind!='read' AND path_normalized IS NOT NULL LIMIT ?",
                (session_id, limit)).fetchall()
        finally:
            conn.close()
        return frozenset(_norm(r[0]) for r in rows if r[0])
    except Exception:      # noqa: BLE001 — safety-neutral: informs sparing, never correctness
        return frozenset()


def file_read_eligible(tool_name: Optional[str], tool_input: Optional[dict], *,
                       edited_paths: frozenset = frozenset()) -> bool:
    """True only for a Read/NotebookRead of a file the agent has NOT already mutated this session."""
    if tool_name not in _FILE_READ_TOOLS:
        return False
    fp = (tool_input or {}).get("file_path")
    if not fp:
        return False
    return _norm(fp) not in edited_paths
