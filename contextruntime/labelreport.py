"""Slice 3A -- observed-label VALIDITY report over a HookJournal (OBSERVE-ONLY, reporter-side).

This does NOT touch the frozen evidence contract (classify.py / hookjournal.py / normalize.py). It
asks a narrower, prior question: are the retrospective labels themselves stable and trustworthy,
BEFORE anyone quotes an ExplorationBypassRate? (A HookJournal alone has no SemanticFS events in its
denominator, so a bypass rate over it would tend to 100% by construction -- that is Slice 3B, after
the cross-channel join. This slice is label validity only.)

RIGHT-CENSORING is handled HERE, in the reporter, not in the classifier. `no_future_mutation ->
EXPLORATION` is final only once the stream is actually closed; in an ongoing session a read can lack a
later edit merely because we haven't OBSERVED it yet. An open stream can only gain future edits, which
can only turn a currently-EXPLORATION read into a precondition/unknown -- never the reverse -- so an
open stream's exploration count is an UPPER BOUND. We therefore mark absence-of-future-edit-derived
labels in OPEN streams as PROVISIONAL and exclude them from the headline exploration denominator,
reporting the distribution both before and after that exclusion. Closure is supplied out-of-band (a
manifest, or a /clear that started a newer lineage epoch) because the journal records no SessionEnd.

The report is AGGREGATE-ONLY (counts, distributions, percentiles, matrices) -- no raw paths -- so it
is privacy-clean by construction. W=16 is the preregistered primary window; {8,32,inf} are reported
as sensitivity, never tuned to maximize exploration.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Optional

from .classify import (CONFIG_REQUIRED, EDIT_PRECONDITION, EXPLORATION, UNKNOWN, VERIFICATION,
                       classify_reads)
from .normalize import to_events

REPORT_SCHEMA_VERSION = "label-report-0.1.0"
PRIMARY_WINDOW = 16
INF_WINDOW = 10 ** 12                       # effectively unbounded; distances never exceed it
DEFAULT_WINDOWS = (8, 16, 32, INF_WINDOW)

# Labels whose truth depends on the ABSENCE of a future edit of the path -- the only ones an
# as-yet-unobserved future edit could flip. In an OPEN stream these are provisional (right-censored).
# ALL four arise only in classify's "no future mutation of p" branch (EXPLORATION, then the secondary
# pass -> VERIFICATION / CONFIG / prior_unverified_mutation-UNKNOWN). `read_version_race` is NOT here:
# it is assigned before a future edit is even considered, so it is future-invariant, not flippable.
_ABSENCE_DERIVED_REASONS = frozenset({"no_future_mutation", "post_edit_reread", "config_path_heuristic",
                                      "prior_unverified_mutation"})
_ALL_CLASSES = (EXPLORATION, EDIT_PRECONDITION, VERIFICATION, CONFIG_REQUIRED, UNKNOWN)


def _win_key(w) -> str:
    return "inf" if w >= INF_WINDOW else str(w)


def _pct(part: int, whole: int):
    return round(100.0 * part / whole, 2) if whole else None


def _percentile(sorted_vals, q):
    """Linear-interpolation percentile (numpy-default 'linear'), stdlib-only. q in [0,100]."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 3)


def _lineage_base(row) -> Optional[str]:
    """The (session, agent) lineage a stream belongs to, taken from the row's OWN identity columns
    when present -- NOT by splitting stream_key, whose agent segment can itself contain ':' (e.g. a
    plugin-scoped agent id like 'my-plugin:reviewer'). Falls back to the key prefix for bare rows."""
    sid = row.get("session_id") if isinstance(row, dict) else None
    if sid is not None:
        aid = row.get("agent_id") or "main"
        return f"{sid}\x00{aid}"                     # NUL can't appear in a session/agent id
    sk = row.get("stream_key") if isinstance(row, dict) else None
    return sk.rpartition(":")[0] if sk else None


def _epoch_of(stream_key) -> Optional[int]:
    ep = stream_key.rpartition(":")[2]
    return int(ep) if ep.isdigit() else None


def stream_closure(rows, manifest: Optional[dict] = None) -> dict:
    """stream_key -> {closed, closure_reason, closed_at_seq}. A stream is closed if the manifest says
    so, OR a NEWER lineage epoch exists for the same (session, agent) -- a /clear closed the prior
    epoch. Lineage grouping uses the identity columns, not a fragile split of the composite key."""
    manifest = manifest or {}
    by_key = {e["stream_key"]: e for e in manifest.get("streams", []) if e.get("stream_key")}
    max_seq, max_epoch, base_of = {}, {}, {}
    for r in rows:
        sk = r["stream_key"]
        if not sk:
            continue
        max_seq[sk] = max(max_seq.get(sk, 0), r["seq"])
        base_of[sk] = _lineage_base(r)
        ep = _epoch_of(sk)
        if base_of[sk] is not None and ep is not None:
            max_epoch[base_of[sk]] = max(max_epoch.get(base_of[sk], -1), ep)
    out = {}
    for sk in max_seq:
        ep = _epoch_of(sk)
        closed, reason, closed_at = False, None, max_seq[sk]
        if sk in by_key:
            m = by_key[sk]
            closed = bool(m.get("closed", False))
            reason = m.get("closure_reason") or ("manifest_marked_closed" if closed else None)
            if m.get("closed_at_seq") is not None:
                closed_at = m["closed_at_seq"]
        elif base_of[sk] is not None and ep is not None and ep < max_epoch.get(base_of[sk], -1):
            closed, reason = True, "superseded_clear_epoch"
        out[sk] = {"closed": closed, "closure_reason": reason, "closed_at_seq": closed_at}
    return out


def _capture_integrity(conn, rows, events) -> dict:
    from .hookjournal import HookJournal  # reuse the canonical derived-ratio computation
    stats = HookJournal.capture_stats(_ConnShim(conn))
    read_rows = [r for r in rows if r["kind"] == "read"]
    edit_rows = [r for r in rows if r["kind"] == "edit"]
    tok_attr = Counter(r["token_attribution"] or "none" for r in read_rows)
    classified_read_ids = {e["event_id"] for e in events if e["kind"] == "read"}
    dropped = sum(1 for r in read_rows if r["event_id"] not in classified_read_ids)
    return {
        "deliveries": stats.get("deliveries", 0),
        "errors": stats.get("errors", 0),
        "delivery_success_ratio": stats.get("delivery_success_ratio"),
        "pre_tool_calls_seen": stats.get("pre_tool_calls_seen", 0),
        "batch_tool_calls_resolved": stats.get("batch_tool_calls_resolved", 0),
        "pre_capture_rate": stats.get("pre_capture_rate"),
        "bash_calls_seen": stats.get("bash_calls_seen", 0),
        "unknown_bash_calls": stats.get("unknown_bash_calls", 0),
        "bash_unknown_share": stats.get("bash_unknown_share"),
        "pending_tools": conn.execute("SELECT COUNT(*) c FROM pending_tools").fetchone()["c"],
        "version_status_counts": dict(Counter(r["version_status"] for r in read_rows)),
        "mutation_status_counts": dict(Counter(r["mutation_status"] for r in edit_rows)),
        "token_attribution_counts": dict(tok_attr),
        # missing OR ambiguous: not just non-'attributed', but ALSO 'attributed' rows with a NULL
        # weight (single-path multimodal reads) -- otherwise this self-check is blind to them.
        "reads_missing_or_ambiguous_token_weight":
            sum(1 for r in read_rows
                if r["token_attribution"] != "attributed" or r["model_visible_tokens"] is None),
        "reads_dropped_pre_classify": dropped,
        "reads_classified": len(classified_read_ids),
        "edits": len(edit_rows),
    }


class _ConnShim:
    """Lets HookJournal.capture_stats(self) run against a bare connection (it only uses self.conn)."""
    def __init__(self, conn):
        self.conn = conn


def _distribution(labels_by_read) -> dict:
    counts = Counter(l.observed_class for l in labels_by_read.values())
    total = sum(counts.values())
    return {
        "counts": {c: counts.get(c, 0) for c in _ALL_CLASSES},
        "percent": {c: _pct(counts.get(c, 0), total) for c in _ALL_CLASSES},
        "total": total,
    }


def build_report(db_path: str, *, manifest: Optional[dict] = None, windows=DEFAULT_WINDOWS,
                 primary: int = PRIMARY_WINDOW, classifier_sha: Optional[str] = None,
                 client_version: Optional[str] = None) -> dict:
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM tool_events ORDER BY seq")]
    events = to_events(rows)
    row_by_id = {r["event_id"]: r for r in rows}
    read_events = [e for e in events if e["kind"] == "read"]

    closure = stream_closure(rows, manifest)

    # classify once per window; keep the full Label (reason/source/grade/evidence) per read
    labels_by_w = {w: classify_reads(events, window=w, distance_key="step") for w in windows}
    primary_labels = labels_by_w[primary]

    def is_provisional(eid) -> bool:
        lab = primary_labels.get(eid)
        if lab is None or lab.reason not in _ABSENCE_DERIVED_REASONS:
            return False
        row = row_by_id[eid]
        cl = closure.get(row["stream_key"], {})
        if not cl.get("closed"):
            return True                                  # open stream -> right-censored
        # closed stream: final ONLY within the observed-closed region. A read whose seq is BEYOND the
        # declared closed_at_seq happened after observation ended, so its "no future edit" is not
        # trustworthy -- keep it provisional rather than counting it as settled.
        closed_at = cl.get("closed_at_seq")
        return closed_at is not None and row["seq"] > closed_at

    # ---- provenance ------------------------------------------------------
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    est_ids = {r["token_estimator_id"] for r in rows if r["token_estimator_id"]}
    with open(db_path, "rb") as fh:
        journal_sha = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    provenance = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "hook_schema_version": meta.get("hook_schema_version"),
        "classifier_sha": classifier_sha,
        "journal_sha256": journal_sha,
        "client_version": client_version,                       # unknown unless stamped
        "token_estimator_id": sorted(est_ids)[0] if len(est_ids) == 1 else sorted(est_ids),
        "primary_window": primary,
        "windows": [_win_key(w) for w in windows],
        "closure_manifest": closure,
        "reads_classified": len(read_events),
        "edits": sum(1 for e in events if e["kind"] == "edit"),
        "streams": len({r["stream_key"] for r in rows if r["stream_key"]}),
    }

    # ---- capture integrity ----------------------------------------------
    integrity = _capture_integrity(conn, rows, events)

    # ---- primary labels (uncensored) at W=primary -----------------------
    dist = _distribution(primary_labels)
    labels_primary = {
        "window": primary,
        **dist,
        "by_classification_source": dict(Counter(l.classification_source for l in primary_labels.values())),
        "by_evidence_grade": dict(Counter(l.evidence_grade for l in primary_labels.values())),
        "by_reason": dict(Counter(l.reason for l in primary_labels.values())),
        "provisional_reads": sum(1 for eid in primary_labels if is_provisional(eid)),
    }

    # ---- tokens: event-share on the full CLASSIFIED set, token-share on the cleanly-ATTRIBUTED
    # subset. "Attributed" for token accounting requires ALL THREE: the read is classified (in
    # primary_labels), token_attribution=='attributed', AND a non-NULL numeric weight. A single-path
    # MULTIMODAL read (image/PDF) is stamped attributed with NULL tokens upstream -- it must NOT be
    # counted as covered nor `or 0`-collapsed to zero cost (those are the largest reads). Ranging over
    # classified reads only also keeps a failed-but-attributed read from pushing coverage over 100%.
    total_reads = len(primary_labels)
    attributed_ids = {eid for eid in primary_labels
                      if row_by_id[eid]["token_attribution"] == "attributed"
                      and row_by_id[eid]["model_visible_tokens"] is not None}
    event_share, token_share = defaultdict(int), defaultdict(int)
    attributed_tokens_total = 0
    for eid, lab in primary_labels.items():
        event_share[lab.observed_class] += 1
        if eid in attributed_ids:
            tok = row_by_id[eid]["model_visible_tokens"]           # guaranteed non-NULL by the filter
            token_share[lab.observed_class] += tok
            attributed_tokens_total += tok
    tokens = {
        "note": ("ESTIMATED attributed model-visible tokens (%s); a read counts as attributed ONLY "
                 "with a non-NULL numeric weight -- multimodal/ambiguous reads are EXCLUDED from "
                 "token-share and from coverage, never counted as zero cost" % provenance["token_estimator_id"]),
        "attribution_coverage": _pct(len(attributed_ids), total_reads),
        "reads_attributed": len(attributed_ids),
        "reads_unattributed": total_reads - len(attributed_ids),
        "attributed_tokens_total": attributed_tokens_total,
        "event_share": {c: _pct(event_share.get(c, 0), total_reads) for c in _ALL_CLASSES},
        "token_share_attributed": {c: _pct(token_share.get(c, 0), attributed_tokens_total)
                                   for c in _ALL_CLASSES},
    }

    # ---- causal-distance evidence for EDIT_PRECONDITION at W=primary -----
    dists, seq_fallback = [], 0
    for eid, lab in primary_labels.items():
        if lab.observed_class != EDIT_PRECONDITION:
            continue
        dinfo = (lab.evidence or {}).get("distance", {})
        if "step" in dinfo:
            dists.append(dinfo["step"])
        elif "seq" in dinfo:
            seq_fallback += 1
    dists.sort()

    def _bucket(d):
        for hi, name in ((0, "0"), (1, "1"), (2, "2"), (4, "3-4"), (8, "5-8"), (16, "9-16"),
                         (32, "17-32")):
            if d <= hi:
                return name
        return ">32"
    causal = {
        "window": primary, "metric": "step", "n": len(dists),
        "p50": _percentile(dists, 50), "p90": _percentile(dists, 90),
        "p95": _percentile(dists, 95), "max": (dists[-1] if dists else None),
        "histogram": dict(Counter(_bucket(d) for d in dists)),
        "seq_fallback_count": seq_fallback,
    }

    # ---- window sensitivity + stability ---------------------------------
    class_counts_by_w, unknown_reasons_by_w = {}, {}
    for w in windows:
        lw = labels_by_w[w]
        class_counts_by_w[_win_key(w)] = {c: sum(1 for l in lw.values() if l.observed_class == c)
                                          for c in _ALL_CLASSES}
        unknown_reasons_by_w[_win_key(w)] = dict(Counter(
            l.reason for l in lw.values() if l.observed_class == UNKNOWN))
    all_read_ids = list(primary_labels.keys())
    invariant = sum(1 for eid in all_read_ids
                    if len({labels_by_w[w][eid].observed_class for w in windows}) == 1)
    precond_sets = [{eid for eid in all_read_ids if labels_by_w[w][eid].observed_class == EDIT_PRECONDITION}
                    for w in windows]
    p_primary = {eid for eid in all_read_ids if primary_labels[eid].observed_class == EDIT_PRECONDITION}
    p_intersect = set.intersection(*precond_sets) if precond_sets else set()
    transitions = {}
    for w in windows:
        if w == primary:
            continue
        mat = defaultdict(lambda: defaultdict(int))
        for eid in all_read_ids:
            mat[primary_labels[eid].observed_class][labels_by_w[w][eid].observed_class] += 1
        transitions[_win_key(w)] = {frm: dict(to) for frm, to in mat.items()}
    sensitivity = {
        "class_counts_by_window": class_counts_by_w,
        "unknown_reasons_by_window": unknown_reasons_by_w,
        "labels_total": len(all_read_ids),
        "labels_invariant_all_windows": invariant,
        "invariant_fraction": _pct(invariant, len(all_read_ids)),
        "stability_precondition": (round(len(p_intersect) / len(p_primary), 4) if p_primary else None),
        "stability_precondition_note": "|P8 ∩ P16 ∩ P32 ∩ Pinf| / |P16|",
        "transition_matrices_vs_primary": transitions,
    }

    # ---- censoring ------------------------------------------------------
    # Exclude ONLY provisional (open-stream) EXPLORATION reads -- the sole overcount risk -- from BOTH
    # numerator and denominator. Provisional verification/config are KEPT in the denominator: dropping
    # them would SHRINK the denominator without touching the exploration numerator and thereby INFLATE
    # exploration. (Proof that after <= before always: after-before is proportional to
    # prov_expl*(expl_all - total) <= 0.)
    prov_by_class = Counter(primary_labels[eid].observed_class for eid in primary_labels
                            if is_provisional(eid))
    prov_total = sum(prov_by_class.values())
    prov_expl = prov_by_class.get(EXPLORATION, 0)
    total_p = len(primary_labels)
    expl_all = sum(1 for l in primary_labels.values() if l.observed_class == EXPLORATION)
    final_expl = expl_all - prov_expl
    kept = {eid: lab for eid, lab in primary_labels.items()
            if not (is_provisional(eid) and lab.observed_class == EXPLORATION)}
    before, after = _distribution(primary_labels), _distribution(kept)
    streams_closed = sum(1 for s in closure.values() if s["closed"])
    censoring = {
        "streams_closed": streams_closed,
        "streams_open": len(closure) - streams_closed,
        "provisional_reads_total": prov_total,
        "provisional_by_class": {c: prov_by_class.get(c, 0) for c in _ALL_CLASSES if prov_by_class.get(c)},
        "provisional_exploration_reads_excluded": prov_expl,
        "headline_exploration": {
            "before_pct": _pct(expl_all, total_p),                # provisional exploration counted (upper)
            "after_pct": _pct(final_expl, total_p - prov_expl),   # provisional exploration set aside
            "floor_pct": _pct(final_expl, total_p),               # provisional exploration = non-exploration
            "final_exploration_reads": final_expl,
            "provisional_exploration_reads": prov_expl,
            "denominator_before": total_p,
            "denominator_after": total_p - prov_expl,
            "note": ("Three honest readings of the same evidence: floor_pct <= after_pct <= before_pct. "
                     "before counts provisional (open-stream) exploration AS exploration (upper bound); "
                     "after sets it aside (num+denom); floor treats it as definitely-not-exploration. "
                     "Only provisional EXPLORATION moves -- provisional verification/config stay in every "
                     "denominator, else dropping them would inflate exploration."),
        },
        "labels_before": before,
        "labels_after": after,                                       # provisional exploration removed only
        "headline_exploration_pct_before": _pct(expl_all, total_p),
        "headline_exploration_pct_after": _pct(final_expl, total_p - prov_expl),
        "closure_by_stream": closure,
    }

    conn.close()
    return {
        "provenance": provenance,
        "capture_integrity": integrity,
        "labels_primary": labels_primary,
        "tokens": tokens,
        "causal_distance_precondition": causal,
        "window_sensitivity": sensitivity,
        "censoring": censoring,
    }


def _fmt_row(label, counts, percent):
    parts = []
    for c in _ALL_CLASSES:
        parts.append(f"{c.split('_')[0][:5]:>5}={counts.get(c, 0):>3}({percent.get(c) or 0:>5}%)")
    return f"  {label:<22} " + " ".join(parts)


def format_text(rep: dict) -> str:
    p, ig = rep["provenance"], rep["capture_integrity"]
    L = []
    A = L.append
    A("=" * 78)
    A("  OBSERVED-LABEL VALIDITY REPORT (Slice 3A) -- observe-only, aggregate-only")
    A("=" * 78)
    A("[1] PROVENANCE")
    A(f"    report_schema={p['report_schema_version']}  hook_schema={p['hook_schema_version']}  "
      f"estimator={p['token_estimator_id']}")
    A(f"    classifier_sha={p['classifier_sha']}  client={p['client_version']}")
    A(f"    journal={p['journal_sha256'][:23]}...  primary_window={p['primary_window']}  "
      f"windows={p['windows']}")
    A(f"    reads_classified={p['reads_classified']}  edits={p['edits']}  streams={p['streams']}")
    A("")
    A("[2] CAPTURE INTEGRITY (does the evidence deserve trust?)")
    A(f"    deliveries={ig['deliveries']} errors={ig['errors']} "
      f"success_ratio={ig['delivery_success_ratio']}  pending_tools={ig['pending_tools']}")
    A(f"    pre_capture_rate={ig['pre_capture_rate']} (pre={ig['pre_tool_calls_seen']}/"
      f"batch={ig['batch_tool_calls_resolved']})  bash_unknown_share={ig['bash_unknown_share']} "
      f"(unknown={ig['unknown_bash_calls']}/bash={ig['bash_calls_seen']})")
    A(f"    version_status={ig['version_status_counts']}  mutations={ig['mutation_status_counts']}")
    A(f"    token_attribution={ig['token_attribution_counts']}  "
      f"reads_missing/ambiguous_weight={ig['reads_missing_or_ambiguous_token_weight']}  "
      f"dropped_pre_classify={ig['reads_dropped_pre_classify']}")
    A("")
    lp = rep["labels_primary"]
    A(f"[3] PRIMARY OBSERVED LABELS @ W={lp['window']} (uncensored; n={lp['total']})")
    A(_fmt_row("distribution", lp["counts"], lp["percent"]))
    A(f"    by_source={lp['by_classification_source']}")
    A(f"    by_grade={lp['by_evidence_grade']}")
    A(f"    provisional_reads(open-stream, absence-derived)={lp['provisional_reads']}")
    A("")
    tk = rep["tokens"]
    A("[4] TOKENS (estimated attributed model-visible; NULL != zero cost)")
    A(f"    attribution_coverage={tk['attribution_coverage']}% "
      f"(attributed={tk['reads_attributed']}/unattributed={tk['reads_unattributed']}) "
      f"attributed_tokens={tk['attributed_tokens_total']}")
    A(f"    event_share%={ {k: v for k, v in tk['event_share'].items() if v} }")
    A(f"    token_share_attributed%={ {k: v for k, v in tk['token_share_attributed'].items() if v} }")
    A("")
    cd = rep["causal_distance_precondition"]
    A(f"[5] CAUSAL DISTANCE for EDIT_PRECONDITION @ W={cd['window']} (agent steps; n={cd['n']})")
    A(f"    p50={cd['p50']} p90={cd['p90']} p95={cd['p95']} max={cd['max']}  "
      f"seq_fallback={cd['seq_fallback_count']}")
    A(f"    histogram={cd['histogram']}")
    A("")
    ws = rep["window_sensitivity"]
    A("[6] WINDOW SENSITIVITY {8,16,32,inf} (W=16 preregistered; others sensitivity only)")
    for wk, cc in ws["class_counts_by_window"].items():
        star = " *primary" if wk == str(rep["provenance"]["primary_window"]) else ""
        A(f"    W={wk:<3} {cc}{star}")
    A(f"    labels_invariant_all_windows={ws['labels_invariant_all_windows']}/{ws['labels_total']} "
      f"({ws['invariant_fraction']}%)  stability_precondition={ws['stability_precondition']} "
      f"({ws['stability_precondition_note']})")
    A(f"    transitions_vs_primary={ws['transition_matrices_vs_primary']}")
    A("")
    cn = rep["censoring"]
    he = cn["headline_exploration"]
    A("[7] RIGHT-CENSORING (the last place a correct classifier can still overstate exploration)")
    A(f"    streams: closed={cn['streams_closed']} open={cn['streams_open']}  "
      f"provisional_total={cn['provisional_reads_total']} by_class={cn['provisional_by_class']}")
    A(f"    headline exploration%: FLOOR={he['floor_pct']} <= AFTER={he['after_pct']} "
      f"({he['final_exploration_reads']}/{he['denominator_after']}) <= BEFORE={he['before_pct']} "
      f"({cn['labels_before']['counts'][EXPLORATION]}/{he['denominator_before']})")
    A("    [only provisional EXPLORATION moves between these; verification/config stay in every denom]")
    A(_fmt_row("labels BEFORE", cn["labels_before"]["counts"], cn["labels_before"]["percent"]))
    A(_fmt_row("labels AFTER ", cn["labels_after"]["counts"], cn["labels_after"]["percent"]))
    A("=" * 78)
    return "\n".join(L)


def report_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, sort_keys=True, default=str)
