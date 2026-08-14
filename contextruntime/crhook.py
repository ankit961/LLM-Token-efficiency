"""cr-hook -- the stdin entry point that feeds live Claude Code hook deliveries into a HookJournal.

Each hook delivery is a SEPARATE PROCESS: Claude Code invokes the configured command, writes ONE
JSON object to its stdin, and reads its exit code. So this module does exactly that -- read one JSON
object, dispatch it through HookCapture.on_event (which persists all cross-delivery state in the
journal DB), and exit 0.

It is OBSERVE-ONLY and FAIL-OPEN, twice over: HookCapture.on_event never raises (it rolls back and
logs internally), and this outer layer swallows everything else (bad stdin, an unopenable DB, an
import error) and still exits 0. A hook that exits non-zero or hangs would block or slow the user's
tool call; observation must never do that. It writes to a SEPARATE journal DB (default
~/.claude/contextruntime/hookjournal.db), never the frozen semantic_reads GraphStore.

A single registration handles every event: the payload's `hook_event_name` selects the behavior, and
unknown events are silently ignored, so the same `contextruntime cr-hook --db ...` line can be wired
under PreToolUse / PostToolUse / (PostToolUseFailure) / (PostToolBatch) / UserPromptSubmit /
SessionStart / (SubagentStart) without per-event scripting.
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT_JOURNAL = os.path.expanduser("~/.claude/contextruntime/hookjournal.db")


def run(stdin_text: str, db_path: str) -> int:
    """Record one hook delivery. ALWAYS returns 0 -- never signals failure to the client."""
    try:
        ev = json.loads(stdin_text) if stdin_text and stdin_text.strip() else None
    except Exception:      # noqa: BLE001 -- malformed stdin: nothing to record, still succeed
        return 0
    if not isinstance(ev, dict):
        return 0
    try:
        from .hookjournal import HookCapture, HookJournal
        if db_path not in (":memory:", "::memory::"):
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        j = HookJournal(db_path)
        try:
            HookCapture(j).on_event(ev)
        finally:
            j.close()
    except Exception:      # noqa: BLE001 -- observation must NEVER break or slow the client's tool call
        return 0
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="contextruntime cr-hook",
        description="Feed one Claude Code hook delivery (JSON on stdin) into a HookJournal. Fail-open.")
    ap.add_argument("--db", default=os.environ.get("CR_HOOK_DB", DEFAULT_JOURNAL),
                    help="journal sqlite path (default: $CR_HOOK_DB or ~/.claude/contextruntime/hookjournal.db)")
    args = ap.parse_args(argv)
    try:
        stdin_text = sys.stdin.read()
    except Exception:      # noqa: BLE001 -- even a broken stdin must exit 0
        return 0
    return run(stdin_text, args.db)


if __name__ == "__main__":
    raise SystemExit(main())
