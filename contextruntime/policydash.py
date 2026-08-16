"""Per-session ContextPolicy dashboard (advisory measurement).

Turns captured session data into the "what did the policy see / what did the agent do" view:
it reads native source reads from the frozen HookJournal, rules on each via the slice-1 policy
engine (semantic-first, C10-safe), and joins the SemanticFS read-surface telemetry to see which
opportunities the agent actually took. Read-only over both stores; changes nothing.

  native source reads    file reads of repo source (frozen journal, representation=file)
  semantic opportunities native reads the policy would steer (smaller skeleton, not edit-precondition)
  semantic reads used    read_symbol/read_slice/bundle materializations (MCP telemetry)
  fallbacks              opportunities whose path was NOT served semantically
  raw equivalent tokens  model-visible tokens of the native source reads
  semantic materialized  transport tokens the semantic reads actually cost
  estimated avoided      raw - skeleton over taken-or-available opportunities
  expansions / retries   Context Expansion Debt hops / recovery turns (telemetry proxies)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .policy import RECOMMEND_SEMANTIC, DENY_NUDGE, decide
from .policyprobe import probe_source_read, probe_to_toolcall
from .store import GraphStore


@dataclass
class Dashboard:
    session_id: str | None
    native_source_reads: int = 0
    semantic_opportunities: int = 0
    semantic_reads_used: int = 0
    fallbacks: int = 0
    raw_equivalent_tokens: int = 0
    semantic_materialized_tokens: int = 0
    estimated_avoided_tokens: int = 0
    expansions: int = 0
    retries: int = 0
    opportunity_detail: list = field(default_factory=list)


def _repo_relative(path_norm, repo_relative, repo_root):
    if repo_relative:
        return repo_relative
    if path_norm and repo_root:
        root = repo_root.rstrip("/") + "/"
        if path_norm.startswith(root):
            return path_norm[len(root):]
    return None


def build_dashboard(journal_db: str, graph_db: str, repo_id: str, *,
                    repo_root: str | None = None, session_id: str | None = None) -> Dashboard:
    graph = GraphStore(graph_db)
    try:
        d = Dashboard(session_id=session_id)

        jc = sqlite3.connect(journal_db)
        jc.row_factory = sqlite3.Row
        # every path this session edited — a read of an edited file is edit-precondition-sensitive (C10)
        eq = "SELECT DISTINCT repo_relative, path_normalized FROM tool_events WHERE kind='edit'"
        ep = ()
        if session_id:
            eq += " AND session_id=?"; ep = (session_id,)
        edited = set()
        for r in jc.execute(eq, ep).fetchall():
            rel = _repo_relative(r["path_normalized"], r["repo_relative"], repo_root)
            if rel:
                edited.add(rel)

        rq = ("SELECT * FROM tool_events WHERE kind='read' AND representation='file' AND success=1")
        rp = ()
        if session_id:
            rq += " AND session_id=?"; rp = (session_id,)
        opp_paths = set()
        for r in jc.execute(rq, rp).fetchall():
            rel = _repo_relative(r["path_normalized"], r["repo_relative"], repo_root)
            if rel is None:
                continue                                   # not a repo file — not a source read
            tok = r["model_visible_tokens"] or 0
            d.native_source_reads += 1
            d.raw_equivalent_tokens += tok
            probe = probe_source_read(graph, repo_id, rel, raw_tokens=tok)
            call = probe_to_toolcall(probe, is_edit_target=(rel in edited))
            decision = decide(call)
            if decision.action in (RECOMMEND_SEMANTIC, DENY_NUDGE):
                d.semantic_opportunities += 1
                d.estimated_avoided_tokens += probe.avoidable_tokens
                opp_paths.add(rel)
                d.opportunity_detail.append(
                    {"path": rel, "raw": tok, "skeleton": probe.skeleton_tokens,
                     "avoidable": probe.avoidable_tokens})

        # semantic reads actually taken (MCP read-surface telemetry lives in the same graph db)
        used_paths = set()
        if _has_table(graph.conn, "semantic_reads"):
            sq = "SELECT path, transport_content_tokens, expansion_tokens, recovery_turns FROM semantic_reads"
            sp = ()
            if session_id:
                sq += " WHERE session_id=?"; sp = (session_id,)
            for row in graph.conn.execute(sq, sp).fetchall():
                d.semantic_reads_used += 1
                d.semantic_materialized_tokens += row[1] or 0
                if (row[2] or 0) > 0:
                    d.expansions += 1
                if (row[3] or 0) > 0:
                    d.retries += 1
                if row[0]:
                    used_paths.add(row[0])

        d.fallbacks = sum(1 for p in opp_paths if p not in used_paths)
        return d
    finally:
        graph.close()


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _k(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def format_dashboard(d: Dashboard) -> str:
    L = ["ContextRuntime — advisory", ""]
    L.append(f"native source reads    {d.native_source_reads:>8}")
    L.append(f"semantic opportunities {d.semantic_opportunities:>8}")
    L.append(f"semantic reads used    {d.semantic_reads_used:>8}")
    L.append(f"fallbacks              {d.fallbacks:>8}")
    L.append("")
    L.append(f"raw equivalent tokens  {_k(d.raw_equivalent_tokens):>8}")
    L.append(f"semantic materialized  {_k(d.semantic_materialized_tokens):>8}")
    L.append(f"estimated avoided      {_k(d.estimated_avoided_tokens):>8}")
    L.append("")
    L.append(f"expansions             {d.expansions:>8}")
    L.append(f"retries                {d.retries:>8}")
    return "\n".join(L)
