"""Budgeted dependency-bundle PLANNER (design v1.2 §8, Phase 2.2 + 2.2.1).

Given a root symbol and a token budget, it plans a small graph projection that is
still sufficient — choosing BOTH which symbols and at what representation level
(a 40-token signature can beat a 900-token implementation).

It is a PLANNER, not yet a compiler: token costs are estimates from stored metadata
(line span, signature length). Phase 2.3 adds the representation MATERIALIZER that
renders actual source-derived text and validates the RENDERED token budget.

Objective (multiple-choice knapsack over symbol × level):
    max  Σ U(v, level)   s.t.   Σ tokens(v, level) ≤ B,   one level per symbol
    U = R · C · W · D · F   (structural only — no NL reranker yet, so Gate 2A can
                             isolate structural quality)

We do NOT solve that exactly. We use a **deterministic monotone greedy
approximation**: mandatory set = ROOT ONLY (hard deps are eligible-for-mandatory,
not auto-mandatory — a true required core needs TYPE_USES/signature-dependency data
we don't extract yet), then one budget-independent increment order (add-a-signature /
upgrade-one-level, ranked by value/cost with per-branch diminishing returns), of which
we apply the longest fitting prefix. Prefix ⇒ a larger budget can only add/upgrade
(monotonicity). It can leave a little budget unused (a big increment blocks a later
small one) — the tradeoff for monotone, interpretable budget sweeps.

Safety: HARD (exact/scoped) ranks above SOFT (inferred); soft is never mandatory;
ambiguous/unresolved are never dependencies (ambiguous → compact, repo-scoped hint).
Budget affects disclosure, not confidence — a soft relation stays soft at any budget.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..store import GraphStore

POLICY_VERSION = "bundle-v1.1"

LEVELS = ("signature", "skeleton", "slice", "implementation")
LEVEL_VALUE = {"signature": 1.0, "skeleton": 1.6, "slice": 2.2, "implementation": 3.0}
DEP_EDGES = ("CALLS", "IMPLEMENTS", "IMPORTS")
HARD = {"exact", "scoped"}
SOFT = {"inferred"}
AMBIGUITY_HINT_CAP = 5     # max candidates SHOWN per ambiguous name (never affects selection)


@dataclass
class BundlePolicy:
    max_depth: int = 2
    tokens_per_line: int = 9
    min_signature_tokens: int = 8
    skeleton_frac: float = 0.25
    slice_frac: float = 0.60
    relation_weight: dict = field(default_factory=lambda: {
        "IMPLEMENTS": 1.0, "CALLS": 0.85, "IMPORTS": 0.5})
    distance_decay: float = 0.6      # D = decay ** distance
    branch_decay: float = 0.6        # per already-selected sibling in the same branch
    soft_priority: float = 0.6       # SOFT (inferred) ranks below HARD (exact/scoped)
    root_level: str = "implementation"

    @property
    def version(self) -> str:
        return POLICY_VERSION


@dataclass
class Selected:
    symbol_id: str
    qualified_name: str
    level: str
    tokens: int
    edge: str | None
    match: str | None
    soft: bool
    distance: int
    branch: str | None
    utility: float
    reason: str


@dataclass
class Bundle:
    root: str
    budget: int
    used: int = 0
    policy_version: str = POLICY_VERSION
    budget_status: str = "ok"
    minimum_viable_budget: int = 0
    selected: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    ambiguity_hints: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _level_tokens(row, level: str, pol: BundlePolicy) -> int:
    sig_src = row["signature"] or row["qualified_name"]
    sig = max(pol.min_signature_tokens, math.ceil(len(sig_src) / 4))
    span = ((row["end_line"] or 0) - (row["start_line"] or 0) + 1)
    impl = max(sig, int(span * pol.tokens_per_line))
    if level == "signature":
        return sig
    if level == "implementation":
        return impl
    frac = pol.skeleton_frac if level == "skeleton" else pol.slice_frac
    return sig + int(frac * (impl - sig))


def _collect(store: GraphStore, root_id: str, repo_id: str, pol: BundlePolicy):
    """BFS over resolved dependency edges. Returns (candidates, ambiguity_hints)."""
    candidates: dict[str, dict] = {}
    ambiguities: set = set()
    seen = {root_id}
    frontier = [(root_id, 0, None)]
    while frontier:
        src_id, dist, branch = frontier.pop(0)
        if dist >= pol.max_depth:
            continue
        for e in store.code_edges_from(src_id, DEP_EDGES):
            dst, match, etype = e["dst_id"], e["match_kind"], e["edge_type"]
            if match not in (HARD | SOFT):
                if match == "ambiguous":
                    ambiguities.add(dst.split(":", 1)[-1])
                continue
            row = store.symbol_row(dst)
            if row is None:
                continue
            this_branch = branch or dst
            soft = match in SOFT
            base = (e["confidence"] * pol.relation_weight.get(etype, 0.5)
                    * (pol.distance_decay ** dist)
                    * (pol.soft_priority if soft else 1.0))
            cur = candidates.get(dst)
            if cur is None or base > cur["base_utility"]:
                candidates[dst] = dict(row=row, edge=etype, match=match, soft=soft,
                                       distance=dist + 1, branch=this_branch,
                                       base_utility=base)
            if dst not in seen:
                seen.add(dst)
                frontier.append((dst, dist + 1, this_branch))
    # ambiguity hints — REPO-SCOPED (never leak another repo's symbol names), candidate list
    # CAPPED (a common short name -- get/set/deconstruct/compile -- can collide with 50-80+
    # symbols in a large codebase like django; uncapped, the hint block alone can consume nearly
    # an entire budget on disambiguation noise instead of the requested symbol's own body).
    # Ambiguous names never enter `candidates` above regardless of cap size -- this only bounds
    # what gets DISPLAYED as a diagnostic hint, never affects bundle selection correctness.
    hints = []
    for name in sorted(ambiguities):
        total = store.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE repo_id=? "
            "AND (qualified_name LIKE ? OR qualified_name = ?)",
            (repo_id, f"%.{name}", name)).fetchone()[0]
        cands = [r["qualified_name"] for r in store.conn.execute(
            "SELECT qualified_name FROM symbols WHERE repo_id=? "
            "AND (qualified_name LIKE ? OR qualified_name = ?) ORDER BY qualified_name LIMIT ?",
            (repo_id, f"%.{name}", name, AMBIGUITY_HINT_CAP))]
        hints.append({"name": name, "candidates": cands, "total_candidates": total,
                     "reason": "ambiguous short-name"})
    return candidates, hints


def build_bundle(store: GraphStore, root_symbol_id: str, budget: int,
                 max_depth: int | None = None, policy: BundlePolicy | None = None) -> Bundle:
    pol = policy or BundlePolicy()
    if max_depth is not None:
        pol.max_depth = max_depth
    root = store.symbol_row(root_symbol_id)
    b = Bundle(root=root_symbol_id, budget=budget)
    if root is None:
        b.budget_status = "insufficient"
        b.metrics = {"error": "root symbol not found"}
        return b

    candidates, hints = _collect(store, root_symbol_id, root["repo_id"], pol)
    b.ambiguity_hints = hints

    # MANDATORY = root only (hard deps are eligible-for-mandatory, NOT auto-mandatory).
    root_tokens = _level_tokens(root, pol.root_level, pol)
    b.minimum_viable_budget = root_tokens
    if root_tokens > budget:
        b.budget_status = "insufficient"
        b.metrics = _metrics(b, candidates)
        return b
    used = root_tokens
    b.selected.append(_sel_root(root, root_symbol_id, root_tokens, pol))

    # Per-candidate collapsed cost LADDER: merge levels that round to the same token
    # cost (keep the highest-value one), so tiny symbols aren't stuck at signature and
    # the optimizer never wastes an increment on a zero-cost step.
    for c in candidates.values():
        best_at: dict[int, tuple[int, float]] = {}
        for i, lv in enumerate(LEVELS):
            cost = _level_tokens(c["row"], lv, pol)
            if cost not in best_at or LEVEL_VALUE[lv] > best_at[cost][1]:
                best_at[cost] = (i, LEVEL_VALUE[lv])
        c["ladder"] = sorted((cost, idx, val) for cost, (idx, val) in best_at.items())

    # One budget-independent increment order (add / upgrade along the ladder by
    # value/cost with per-branch diminishing returns); apply the longest fitting
    # prefix. Prefix ⇒ monotone. Generation is bounded by the budget.
    chosen: dict[str, int] = {}                    # sid -> ladder position
    sim: dict[str, int] = {}
    sim_branch: dict[str, int] = {}
    sequence: list[tuple[str, int, int]] = []
    seq_cost = 0
    cap = budget - root_tokens
    while seq_cost <= cap:
        best = None
        for sid, c in candidates.items():
            pos = sim.get(sid, -1)
            ladder = c["ladder"]
            if pos >= len(ladder) - 1:
                continue
            npos = pos + 1
            cur_cost = ladder[pos][0] if pos >= 0 else 0
            cur_val = ladder[pos][2] if pos >= 0 else 0.0
            cost = ladder[npos][0] - cur_cost
            gain = ((ladder[npos][2] - cur_val) * c["base_utility"]
                    * (pol.branch_decay ** sim_branch.get(c["branch"], 0)))
            key = (gain / cost, -cost, sid)
            if best is None or key > best[0]:
                best = (key, sid, npos, cost)
        if best is None:
            break
        _key, sid, npos, cost = best
        if sid not in sim:
            sim_branch[candidates[sid]["branch"]] = sim_branch.get(candidates[sid]["branch"], 0) + 1
        sim[sid] = npos
        sequence.append((sid, npos, cost))
        seq_cost += cost

    for sid, npos, cost in sequence:
        if used + cost > budget:
            break
        chosen[sid] = npos
        used += cost

    for sid in sorted(chosen, key=lambda s: (candidates[s]["distance"], s)):
        c = candidates[sid]
        idx = c["ladder"][chosen[sid]][1]
        level = LEVELS[idx]
        b.selected.append(Selected(
            symbol_id=sid, qualified_name=c["row"]["qualified_name"], level=level,
            tokens=_level_tokens(c["row"], level, pol), edge=c["edge"], match=c["match"],
            soft=c["soft"], distance=c["distance"], branch=c["branch"],
            utility=round(c["base_utility"] * LEVEL_VALUE[level], 4),
            reason=("soft" if c["soft"] else "hard")))
    b.used = used
    for sid, c in sorted(candidates.items()):
        if sid not in chosen:
            b.excluded.append({"symbol_id": sid,
                               "qualified_name": c["row"]["qualified_name"],
                               "reason": "budget", "match": c["match"]})
    b.metrics = _metrics(b, candidates)
    return b


def _sel_root(root, root_id, tokens, pol):
    return Selected(symbol_id=root_id, qualified_name=root["qualified_name"],
                    level=pol.root_level, tokens=tokens, edge=None, match=None,
                    soft=False, distance=0, branch=None, utility=float("inf"),
                    reason="root")


def _metrics(b: Bundle, candidates) -> dict:
    sel = [s for s in b.selected if s.reason != "root"]
    by_level: dict = {}
    for s in b.selected:
        by_level[s.level] = by_level.get(s.level, 0) + s.tokens
    branch_tokens: dict = {}
    for s in b.selected:
        if s.branch:
            branch_tokens[s.branch] = branch_tokens.get(s.branch, 0) + s.tokens
    disc = max(1, b.used - (b.selected[0].tokens if b.selected else 0))
    top_branch = max(branch_tokens.values()) if branch_tokens else 0
    return {
        "budget_requested": b.budget, "budget_consumed": b.used,
        "root_tokens": b.selected[0].tokens if b.selected else 0,
        "hard_candidates": sum(1 for c in candidates.values() if c["match"] in HARD),
        "soft_candidates": sum(1 for c in candidates.values() if c["match"] in SOFT),
        "hard_selected": sum(1 for s in sel if not s.soft),
        "soft_selected": sum(1 for s in sel if s.soft),
        "tokens_by_level": by_level,
        "ambiguities_surfaced": len(b.ambiguity_hints),
        "utility_selected": round(sum(s.utility for s in sel if s.utility != float("inf")), 3),
        "branch_concentration": round(top_branch / disc, 3),
        "minimum_viable_budget": b.minimum_viable_budget,
    }


def format_bundle(b: Bundle) -> str:
    lines = [f"Bundle  root={b.root}  budget={b.budget}  used={b.used}  "
             f"[{b.budget_status}]  policy={b.policy_version}"]
    if b.budget_status == "insufficient":
        lines.append(f"  minimum_viable_budget={b.minimum_viable_budget}")
    lines.append("  selected:")
    for s in b.selected:
        tag = "root" if s.reason == "root" else f"{s.match}{'/soft' if s.soft else ''}"
        u = "∞" if s.utility == float("inf") else s.utility
        lines.append(f"    {s.qualified_name:40s} @{s.level:14s} {s.tokens:5d} tok  "
                     f"d={s.distance} {tag}  u={u}")
    if b.ambiguity_hints:
        lines.append("  ambiguity hints (not dependencies):")
        for h in b.ambiguity_hints:
            lines.append(f"    {h['name']}() -> {h['candidates']}")
    if b.excluded:
        lines.append(f"  excluded ({len(b.excluded)} by budget): "
                     + ", ".join(e["qualified_name"] for e in b.excluded[:6])
                     + (" ..." if len(b.excluded) > 6 else ""))
    return "\n".join(lines)
