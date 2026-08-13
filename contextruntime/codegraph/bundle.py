"""Budgeted dependency-bundle generator (design v1.2 §8, Phase 2.2).

The first component that directly attacks source-token admission: given a root symbol
and a token budget, choose a small graph projection that is still sufficient — selecting
BOTH which symbols and at which representation level (a 40-token signature can beat a
900-token implementation).

Optimization (multiple-choice knapsack over symbol × level):
    max  Σ U(v, level)   s.t.   Σ tokens(v, level) ≤ B,   one level per symbol
    U = R · C · W · D · F      (structural only — no NL reranker yet, so Gate 2A can
                                isolate structural quality)

Solved by an INCREMENTAL greedy: seed the mandatory set (root + a tiny required core of
direct-hard-dependency signatures), then repeatedly apply the best available increment —
add a new symbol at signature, or upgrade a selected symbol one level — by value/cost.
Because it only ever adds/upgrades, a larger budget can never yield less information
(monotonicity). Per-branch diminishing returns stop one dependency subtree from eating
the budget. Deterministic: same graph + root + budget + policy ⇒ identical bundle.

Safety (from Phase 2.1/2.1.1): HARD (exact/scoped) deps are eligible for the mandatory
set; SOFT (inferred) deps are discretionary and NEVER mandatory; ambiguous/unresolved
are never dependencies (ambiguous become compact hints). Budget affects disclosure, not
epistemic confidence — a soft relation stays soft at any budget.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

from ..store import GraphStore

POLICY_VERSION = "bundle-v1"

LEVELS = ("signature", "skeleton", "slice", "implementation")
LEVEL_VALUE = {"signature": 1.0, "skeleton": 1.6, "slice": 2.2, "implementation": 3.0}
DEP_EDGES = ("CALLS", "IMPLEMENTS", "IMPORTS")
HARD = {"exact", "scoped"}
SOFT = {"inferred"}


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
    edge: str | None          # relation that pulled it in (None for root)
    match: str | None         # exact/scoped/inferred
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
    budget_status: str = "ok"                 # ok | insufficient
    minimum_viable_budget: int = 0
    selected: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    ambiguity_hints: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --- token cost per (symbol, level) -----------------------------------------

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


# --- traversal ---------------------------------------------------------------

def _collect(store: GraphStore, root_id: str, pol: BundlePolicy):
    """BFS over resolved dependency edges. Returns (candidates, ambiguity_hints).

    candidates[symbol_id] = dict(row, edge, match, soft, distance, branch, base_utility)
    """
    candidates: dict[str, dict] = {}
    ambiguities: dict[str, set] = {}
    seen = {root_id}
    frontier = [(root_id, 0, None)]              # (symbol_id, distance, branch)
    while frontier:
        src_id, dist, branch = frontier.pop(0)
        if dist >= pol.max_depth:
            continue
        for e in store.code_edges_from(src_id, DEP_EDGES):
            dst, match, etype = e["dst_id"], e["match_kind"], e["edge_type"]
            if match not in (HARD | SOFT):       # ambiguous/unresolved -> not a dependency
                if match == "ambiguous":
                    name = dst.split(":", 1)[-1]
                    ambiguities.setdefault(name, set())
                continue
            row = store.symbol_row(dst)
            if row is None:
                continue
            this_branch = branch or dst          # first hop defines the branch
            base = (e["confidence"] * pol.relation_weight.get(etype, 0.5)
                    * (pol.distance_decay ** dist))
            cur = candidates.get(dst)
            if cur is None or base > cur["base_utility"]:
                candidates[dst] = dict(row=row, edge=etype, match=match,
                                       soft=(match in SOFT), distance=dist + 1,
                                       branch=this_branch, base_utility=base)
            if dst not in seen:
                seen.add(dst)
                frontier.append((dst, dist + 1, this_branch))
    # materialize ambiguity hints with candidate qualified names
    hints = []
    for name in sorted(ambiguities):
        cands = [r["qualified_name"] for r in store.conn.execute(
            "SELECT qualified_name FROM symbols WHERE qualified_name LIKE ? "
            "OR qualified_name = ? ORDER BY qualified_name", (f"%.{name}", name))]
        hints.append({"name": name, "candidates": cands, "reason": "ambiguous short-name"})
    return candidates, hints


# --- the optimizer -----------------------------------------------------------

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

    # mandatory: root (+ tiny required core = direct HARD deps at signature)
    root_tokens = _level_tokens(root, pol.root_level, pol)
    chosen: dict[str, dict] = {}                  # symbol_id -> {level_idx, cand}
    candidates, hints = _collect(store, root_symbol_id, pol)
    b.ambiguity_hints = hints

    core_ids = [sid for sid, c in candidates.items()
                if c["distance"] == 1 and c["match"] in HARD]
    core_tokens = sum(_level_tokens(candidates[sid]["row"], "signature", pol)
                      for sid in core_ids)
    minimum = root_tokens + core_tokens
    b.minimum_viable_budget = minimum
    if minimum > budget:
        # can't even seat root + required core
        b.used = root_tokens if root_tokens <= budget else 0
        if root_tokens <= budget:
            b.selected.append(_sel_root(root, root_symbol_id, root_tokens, pol))
        b.budget_status = "insufficient"
        b.metrics = _metrics(b, candidates, core_ids)
        return b

    used = root_tokens
    b.selected.append(_sel_root(root, root_symbol_id, root_tokens, pol))
    for sid in sorted(core_ids):
        c = candidates[sid]
        tok = _level_tokens(c["row"], "signature", pol)
        chosen[sid] = {"idx": 0, "cand": c}
        used += tok

    # Discretionary: build ONE budget-independent increment order (add-at-signature or
    # upgrade-one-level, ranked by value/cost with per-branch diminishing returns), then
    # apply its longest prefix that fits. Prefix ⇒ larger budget can only add/upgrade
    # (monotonicity), and the order is deterministic.
    sim = dict(chosen)                            # simulated state (idx per symbol)
    sim_branch: dict[str, int] = {}
    for sid in chosen:
        sim_branch[candidates[sid]["branch"]] = sim_branch.get(candidates[sid]["branch"], 0) + 1
    sequence: list[tuple[str, int, int]] = []     # (sid, new_idx, cost)
    while True:
        best = None
        for sid, c in candidates.items():
            cur_idx = sim[sid]["idx"] if sid in sim else -1
            if cur_idx >= len(LEVELS) - 1:
                continue
            new_idx = cur_idx + 1
            cur_tok = _level_tokens(c["row"], LEVELS[cur_idx], pol) if cur_idx >= 0 else 0
            cost = _level_tokens(c["row"], LEVELS[new_idx], pol) - cur_tok
            if cost <= 0:
                continue
            cur_val = c["base_utility"] * LEVEL_VALUE[LEVELS[cur_idx]] if cur_idx >= 0 else 0.0
            new_val = c["base_utility"] * LEVEL_VALUE[LEVELS[new_idx]]
            gain = (new_val - cur_val) * (pol.branch_decay ** sim_branch.get(c["branch"], 0))
            key = (gain / cost, -cost, sid)       # value density; tiebreak cheaper, then id
            if best is None or key > best[0]:
                best = (key, sid, new_idx, cost)
        if best is None:
            break
        _key, sid, new_idx, cost = best
        if sid not in sim:
            sim_branch[candidates[sid]["branch"]] = sim_branch.get(candidates[sid]["branch"], 0) + 1
        sim[sid] = {"idx": new_idx}
        sequence.append((sid, new_idx, cost))

    for sid, new_idx, cost in sequence:
        if used + cost > budget:                  # STOP at first non-fit -> monotone prefix
            break
        chosen[sid] = {"idx": new_idx, "cand": candidates[sid]}
        used += cost

    # materialize selected
    for sid in sorted(chosen, key=lambda s: (candidates[s]["distance"], s)):
        c, idx = candidates[sid], chosen[sid]["idx"]
        level = LEVELS[idx]
        b.selected.append(Selected(
            symbol_id=sid, qualified_name=c["row"]["qualified_name"], level=level,
            tokens=_level_tokens(c["row"], level, pol), edge=c["edge"], match=c["match"],
            soft=c["soft"], distance=c["distance"], branch=c["branch"],
            utility=round(c["base_utility"] * LEVEL_VALUE[level], 4),
            reason=("required-core" if (sid in core_ids and idx == 0) else "selected")))
    b.used = used
    for sid, c in sorted(candidates.items()):
        if sid not in chosen:
            b.excluded.append({"symbol_id": sid,
                               "qualified_name": c["row"]["qualified_name"],
                               "reason": "budget", "match": c["match"]})
    b.metrics = _metrics(b, candidates, core_ids)
    return b


def _sel_root(root, root_id, tokens, pol):
    return Selected(symbol_id=root_id, qualified_name=root["qualified_name"],
                    level=pol.root_level, tokens=tokens, edge=None, match=None,
                    soft=False, distance=0, branch=None,
                    utility=float("inf"), reason="root")


def _metrics(b: Bundle, candidates, core_ids) -> dict:
    sel = [s for s in b.selected if s.reason != "root"]
    by_level = {}
    for s in b.selected:
        by_level[s.level] = by_level.get(s.level, 0) + s.tokens
    branch_tokens = {}
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
        tag = "root" if s.reason == "root" else (f"{s.match}{'/soft' if s.soft else ''}")
        lines.append(f"    {s.qualified_name:40s} @{s.level:14s} {s.tokens:5d} tok  "
                     f"d={s.distance} {tag}  u={s.utility if s.utility!=float('inf') else '∞'}")
    if b.ambiguity_hints:
        lines.append("  ambiguity hints (not dependencies):")
        for h in b.ambiguity_hints:
            lines.append(f"    {h['name']}() -> {h['candidates']}")
    if b.excluded:
        lines.append(f"  excluded ({len(b.excluded)} by budget): "
                     + ", ".join(e["qualified_name"] for e in b.excluded[:6])
                     + (" ..." if len(b.excluded) > 6 else ""))
    return "\n".join(lines)
