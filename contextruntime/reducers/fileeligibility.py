"""B2.2 — edit-safety eligibility for file-read residency reduction.

A file read is only compacted if it is safe: the agent has NOT already mutated that file this
session (a mutated file is an active edit target — keep its exact content resident so the next Edit
matches). Files edited LATER are still compacted at read time (we can't see the future), and the
compact-until-edit residency model relies on the agent re-Reading — which materializes exact content
— before it edits a compacted region.

TWO DISTINCT safety notions — do not conflate them:
  * MECHANICAL safety (guaranteed here): the exact raw is always in the CAS (result:// recovery),
    and the Edit tool matches its old_string against the file ON DISK — so a compacted read can
    never CORRUPT an edit or lose bytes; the worst mechanical outcome is a forced re-read.
  * SEMANTIC / task safety (NOT guaranteed by CAS + disk-match): an agent reasoning from an
    incomplete skeleton could choose a wrong implementation and emit a syntactically valid but WRONG
    patch, and may never know to call result://. Whether omission changes the agent's reasoning or
    final patch quality is measured by B2.3 (offline edit-recall) and, decisively, by B2.4 + real
    SWE-bench grading — not asserted here.

This module is pure/read-only and fail-open: any doubt (incl. unknown mutation state ⇒ edited_paths
is None) ⇒ NOT eligible ⇒ the file passes through. It is NOT wired into the frozen B1 hook; B2 stays
behind CR_REDUCE_FILES until B2.4.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

_FILE_READ_TOOLS = frozenset({"Read", "NotebookRead"})


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").strip().rstrip("/")


def edited_paths_from_journal(journal_db: Optional[str], session_id: Optional[str],
                              limit: int = 1000) -> Optional[frozenset]:
    """Paths the session has MUTATED (any non-read tool event with a path) — the files that must stay
    resident in full. TRI-STATE, so uncertainty is fail-open (not silently 'nothing edited'):
      * frozenset(...)  — KNOWN mutation set (may be empty = session has mutated nothing yet)
      * None            — UNKNOWN (no journal/session, or unreadable/locked/corrupt) ⇒ the caller
                          MUST pass the read through; we cannot assert a file is safe to compact
                          without knowing what has been edited.
    Read-only sqlite; any error ⇒ None (unknown), never an empty set."""
    if not journal_db or not session_id:
        return None
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
    except Exception:      # noqa: BLE001 — unreadable journal ⇒ UNKNOWN ⇒ caller passes through
        return None


def file_read_eligible(tool_name: Optional[str], tool_input: Optional[dict], *,
                       edited_paths: Optional[frozenset] = frozenset()) -> bool:
    """True only for a Read/NotebookRead of a file KNOWN not to have been mutated this session.
    `edited_paths is None` (unknown mutation state) ⇒ NOT eligible ⇒ pass through (fail-open)."""
    if tool_name not in _FILE_READ_TOOLS:
        return False
    fp = (tool_input or {}).get("file_path")
    if not fp:
        return False
    if edited_paths is None:                       # unknown mutation state ⇒ fail-open
        return False
    return _norm(fp) not in edited_paths
