"""The occupancy and economic ledgers, computed as queries over the residency
graph (design §4). Two ledgers, never one: cached tokens are cheap but still
occupy the window, so attention burden and dollar cost are reported separately.

Every report is stamped with the Capability Profile / evidence grade that produced
it (design C11) — see cli.py.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .model import LedgerEvent
from .pricing import load_pricing, price_for
from .store import GraphStore


@dataclass
class LedgerReport:
    # exact (reconciled from usage)
    occupancy_tokens: int = 0
    input_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    cost_verified: bool = True
    n_requests: int = 0
    # attributed (estimated, from the residency graph)
    attributed_token_turns: int = 0
    token_turns_by_kind: dict = field(default_factory=dict)
    # strict-tier waste (measured)
    duplicate_events: int = 0
    duplicate_tokens: int = 0
    models: dict = field(default_factory=dict)
    evidence_grade: str = "C"

    def to_dict(self) -> dict:
        return {**self.__dict__}


def compute(store: GraphStore, pricing_path=None) -> LedgerReport:
    table = load_pricing(pricing_path)
    rep = LedgerReport()

    # --- economic + occupancy: exact, from Request nodes ---------------------
    verified_all = True
    for r in store.requests():
        rep.n_requests += 1
        occ = r["input_tokens"] + r["cache_read"] + r["cache_creation"]
        rep.occupancy_tokens += occ
        rep.input_tokens += r["input_tokens"]
        rep.cache_read += r["cache_read"]
        rep.cache_creation += r["cache_creation"]
        rep.output_tokens += r["output_tokens"]
        rep.models[r["model"]] = rep.models.get(r["model"], 0) + 1
        p, verified = price_for(r["model"], table)
        verified_all = verified_all and verified
        rep.est_cost_usd += (
            r["input_tokens"] * p.get("input", 0)
            + r["cache_read"] * p.get("cache_read", 0)
            + r["cache_creation"] * p.get("cache_write_1h", p.get("cache_write_5m", 0))
            + r["output_tokens"] * p.get("output", 0)
        ) / 1e6
    rep.cost_verified = verified_all

    # --- attributed token-turns: estimated, from RESIDENT_IN spans -----------
    kind_of = {o["content_id"]: o["kind"] for o in store.objects()}
    tok_of = {o["content_id"]: o["token_est"] for o in store.objects()}
    by_kind: Counter = Counter()
    for e in store.edges("RESIDENT_IN"):
        props = json.loads(e["props"]) if e["props"] else {}
        span = max(1, props.get("exit_turn", 0) - props.get("entry_turn", 0) + 1)
        tt = tok_of.get(e["src_id"], 0) * span
        rep.attributed_token_turns += tt
        by_kind[kind_of.get(e["src_id"], "?")] += tt
    rep.token_turns_by_kind = dict(by_kind.most_common())

    # --- strict-tier waste: measured, from DUPLICATE_OF ----------------------
    for e in store.edges("DUPLICATE_OF"):
        rep.duplicate_events += 1
        rep.duplicate_tokens += tok_of.get(e["src_id"], 0)

    return rep


def format_report(rep: LedgerReport, profile_stamp: str) -> str:
    def fmt(n):
        n = float(n)
        for u, s in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
            if abs(n) >= u:
                return f"{n/u:.2f}{s}"
        return str(int(n))

    lines = [
        "ContextScope ledger (Phase 0b)",
        f"  capability profile : {profile_stamp}",
        f"  evidence grade     : {rep.evidence_grade}",
        "",
        "  ATTENTION (occupancy, exact):",
        f"    requests         : {rep.n_requests:,}",
        f"    occupancy        : {fmt(rep.occupancy_tokens)} tokens"
        f"  (cache_read {100*rep.cache_read/max(rep.occupancy_tokens,1):.0f}%)",
        f"    output           : {fmt(rep.output_tokens)}",
        "",
        f"  COST (economic, {'estimated/UNVERIFIED prices' if not rep.cost_verified else 'estimated'}):",
        f"    est. spend       : ${rep.est_cost_usd:,.2f}",
        "",
        "  ATTRIBUTED token-turns by kind (estimated):",
    ]
    tot = sum(rep.token_turns_by_kind.values()) or 1
    for k, tt in rep.token_turns_by_kind.items():
        lines.append(f"    {k:18s} {fmt(tt):>8}  ({100*tt/tot:.1f}%)")
    lines += [
        "",
        f"  MEASURED redundancy (Tier A): {rep.duplicate_events:,} duplicate deliveries, "
        f"{fmt(rep.duplicate_tokens)} tokens",
    ]
    return "\n".join(lines)
