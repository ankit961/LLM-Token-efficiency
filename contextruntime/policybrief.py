"""ContextPolicy advisory brief — the SessionStart steering channel.

PreToolUse stdout is invisible to the model, so per-call steering can't be advisory (only a
hard deny would reach the agent). The contract-correct advisory channel is SessionStart: Claude
Code injects a SessionStart hook's plain-text stdout into the model's context. So `cr-policy`
emits, once per session, a short standing brief that makes the agent aware of the semantic read
surface and the policy heuristic (semantic-first for understanding, native for editing — which is
exactly C10). It is advisory and fail-open: on any error, or an unindexed repo, it prints nothing
and the session proceeds as native Claude.
"""
from __future__ import annotations

import json
import sys

from .store import GraphStore


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
        "[ContextRuntime — advisory, observe-only]\n"
        f"This repository is indexed ({n} symbols) with a semantic read surface exposed as MCP "
        "tools (server: contextruntime):\n"
        "  • read_symbol(symbol, budget) — a symbol plus its budgeted dependency neighborhood, as source\n"
        "  • read_slice / context_search / find_callers — targeted views that return handles, not full dumps\n"
        "When you need to UNDERSTAND source, prefer these over a raw Read of a whole file or a broad "
        "grep: they materialize a smaller, budgeted bundle for the same understanding. For files you "
        "intend to EDIT, read them natively as usual. This is advisory — native Read/Grep/Bash always "
        "work and are never blocked."
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
