"""Reduction planner — measure what ContextReduce would save, in OBSERVE mode.

This is the Experiment-B dataset (design §15): over an ingested residency graph,
apply reducers to every reducible tool-result object, record a REDUCES edge
(reduced -> raw) with the raw/reduced token counts and handle, and report the
aggregate reduction. No model requests, no live hooks — pure measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import should_reduce
from .library import reduce_result
from ..store import GraphStore


@dataclass
class ReductionReport:
    considered: int = 0
    reduced: int = 0
    skipped: dict = field(default_factory=dict)      # reason -> count
    raw_tokens: int = 0
    reduced_tokens: int = 0
    invariant_failures: int = 0
    by_reducer: dict = field(default_factory=dict)   # reducer -> {raw, reduced, n}

    @property
    def saved_tokens(self) -> int:
        return max(0, self.raw_tokens - self.reduced_tokens)

    @property
    def ratio(self) -> float:
        return self.reduced_tokens / self.raw_tokens if self.raw_tokens else 1.0


def scan_graph(store: GraphStore, write_edges: bool = True) -> ReductionReport:
    rep = ReductionReport()
    # blob samples give us representative text; for measurement we reduce the sample
    # and scale — but where we have the full sample we reduce it directly.
    samples = {r["content_hash"]: (r["sample"] or "") for r in
               store.conn.execute("SELECT content_hash, sample FROM blobs")}

    for o in list(store.objects()):
        rep.considered += 1
        ok, reason = should_reduce(o["kind"], o["token_est"])
        if not ok:
            rep.skipped[reason.split(" (")[0]] = rep.skipped.get(reason.split(" (")[0], 0) + 1
            continue
        raw = samples.get(o["content_hash"], "")
        if not raw:
            rep.skipped["no sample captured"] = rep.skipped.get("no sample captured", 0) + 1
            continue
        red = reduce_result(None, {}, raw, kind=o["kind"])
        # scale the reducer's ratio to the object's full token estimate
        est_reduced = int(o["token_est"] * red.ratio)
        rep.reduced += 1
        rep.raw_tokens += o["token_est"]
        rep.reduced_tokens += est_reduced
        if not red.invariants_ok:
            rep.invariant_failures += 1
        b = rep.by_reducer.setdefault(red.reducer, {"raw": 0, "reduced": 0, "n": 0})
        b["raw"] += o["token_est"]; b["reduced"] += est_reduced; b["n"] += 1
        if write_edges:
            store.add_edge(f"{o['content_id']}::reduced", o["content_id"], "REDUCES",
                           {"handle": red.handle, "reducer": red.reducer,
                            "raw_tokens": o["token_est"], "reduced_tokens": est_reduced},
                           session_id=o["session_id"])
            store.conn.execute(
                "UPDATE objects SET reducer_applied=1 WHERE content_id=?", (o["content_id"],))
    if write_edges:
        store.commit()
    return rep


def format_report(rep: ReductionReport, evidence_grade: str = "C") -> str:
    def fmt(n):
        n = float(n)
        for u, s in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
            if abs(n) >= u:
                return f"{n/u:.2f}{s}"
        return str(int(n))

    pct = 100 * (1 - rep.ratio)
    lines = [
        "ContextReduce — Experiment-B (observe mode)",
        f"  evidence grade   : {evidence_grade}  (estimates: chars/4, sampled bodies)",
        f"  objects considered: {rep.considered:,}   reduced: {rep.reduced:,}",
        f"  raw tokens       : {fmt(rep.raw_tokens)}",
        f"  reduced tokens   : {fmt(rep.reduced_tokens)}   "
        f"(**{pct:.0f}% reduction** on reducible results)",
        f"  invariant failures: {rep.invariant_failures}"
        + ("  <- reducer dropped a critical line; investigate" if rep.invariant_failures else ""),
        "",
        "  by reducer:",
    ]
    for name, b in sorted(rep.by_reducer.items(), key=lambda kv: -kv[1]["raw"]):
        r = 100 * (1 - (b["reduced"] / b["raw"])) if b["raw"] else 0
        lines.append(f"    {name:9s} n={b['n']:<5} {fmt(b['raw']):>8} -> {fmt(b['reduced']):>8}  ({r:.0f}%)")
    lines += ["", "  skipped (retention):"]
    for reason, n in sorted(rep.skipped.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {n:<6} {reason}")
    lines += ["",
              "  NOTE: observe mode. Actual PostToolUse enforcement is version-gated —",
              "  run `contextruntime doctor`; reduction applies only if the client supports it."]
    return "\n".join(lines)
