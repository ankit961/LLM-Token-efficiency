"""ContextPolicy advisory brief — the SessionStart steering channel.

PreToolUse stdout is invisible to the model, so per-call steering can't be advisory (only a
hard deny would reach the agent). The contract-correct advisory channel is SessionStart: Claude
Code injects a SessionStart hook's plain-text stdout into the model's context. So `cr-policy`
emits, once per session, a short standing brief that makes the agent aware of the semantic read
surface and the policy heuristic (semantic-first for understanding, native for editing — which is
exactly C10). It is advisory and fail-open: on any error, or an unindexed repo, it prints nothing
and the session proceeds as native Claude.

BRIEF_VERSION v2-locate-then-examine (2026-08-16): v1 put the whole imperative on read_symbol
("call read_symbol FIRST"), which needs an exact symbol name up front. Live evidence from the
Semantic Admission Experiment v1 validation batch showed 0/6 spontaneous adoption on real
SWE-bench debugging tasks even with a confirmed-delivered v1 brief + a working, budget-correct
tool -- the agent instead did substantial native exploration (e.g. 16 native reads on one task),
consistent with not having a symbol name to give read_symbol at the START of a debugging task.
v2 maps the brief to the actual two-phase workflow: LOCATE (context_search, which takes a
free-text query and returns handles -- the semantic substitute for a broad native Grep, usable
without knowing an exact name) THEN EXAMINE (read_symbol, once a specific symbol is known). Kept
strictly advisory -- no mechanism change, still delivered via --append-system-prompt, native
fallback always available.
"""
from __future__ import annotations

import json
import sys

from .store import GraphStore

BRIEF_VERSION = "v2-locate-then-examine"


def _symbol_count(graph_db: str, repo_id: str) -> int:
    try:
        g = GraphStore(graph_db)
        try:
            n = g.conn.execute(
                "SELECT count(*) FROM symbols WHERE repo_id=? AND kind IN "
                "('function','method','class','interface','type','constant','test')",
                (repo_id,)).fetchone()[0]
            return int(n or 0)
        finally:
            g.close()
    except Exception:      # noqa: BLE001 — advisory brief must never break a session
        return 0


def build_brief(graph_db: str, repo_id: str) -> str:
    """The standing advisory. Empty string when there's nothing to steer to (fail-open)."""
    n = _symbol_count(graph_db, repo_id)
    if n <= 0:
        return ""
    return (
        "[ContextRuntime — advisory]\n"
        f"This repository is indexed ({n} symbols) with a semantic read surface exposed as MCP tools "
        "(server: contextruntime):\n"
        "  • context_search(query) — locate relevant code by keyword/name; returns compact HANDLES, "
        "not full-file dumps\n"
        "  • read_symbol(name) — once you know WHICH symbol matters, its budgeted source + dependency "
        "neighborhood (accepts a bare name)\n"
        "  • read_slice / find_callers — other targeted views, also handle-based\n"
        "POLICY: when you don't yet know which function/class is relevant (a bug report, a traceback, "
        "\"where does X happen\"), call context_search(query) FIRST instead of a broad native Grep/Read "
        "— same move, smaller. Once you know the specific symbol, call read_symbol(name) instead of a "
        "native Read to examine it. Read a whole file natively only when these don't cover what you "
        "need, or when you are about to EDIT it. Native Read/Grep/Bash always work and are never "
        "blocked — this is advisory, not a gate."
    )


def run(stdin_text: str, graph_db: str, repo_id: str) -> int:
    """SessionStart hook entry: print the brief (model-visible) and exit 0. Fail-open twice over."""
    try:
        try:
            payload = json.loads(stdin_text) if stdin_text.strip() else {}
        except Exception:      # noqa: BLE001
            payload = {}
        # only speak on SessionStart; silently ignore anything else it may be wired to
        if payload.get("hook_event_name") not in (None, "SessionStart"):
            return 0
        brief = build_brief(graph_db, repo_id)
        if brief:
            sys.stdout.write(brief + "\n")
    except Exception:          # noqa: BLE001 — never break the session
        pass
    return 0
