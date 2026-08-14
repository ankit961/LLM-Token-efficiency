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

The report is AGGREGATE-ONLY (counts, distributions, percentiles, matrices) -- no raw paths, and
stream identifiers are PSEUDONYMIZED (stream_001..) so no session/agent lineage leaks into the shared
artifact (the raw map is emitted only under include_stream_map, for a local/debug file). W=16 is the
preregistered primary window and the CANONICAL default LOCKS primary=16 / windows={8,16,32,inf};
experimentation must pass canonical=False (stamped canonical_report=false) so a tuned window set can
never masquerade as the preregistration. Exploration is reported as a censoring INTERVAL
(floor <= after <= before) for BOTH events and fully-measured tokens, plus a complete-case (closed
streams only) estimate -- never a single point until every stream closes. Agent-step integrity is a
validity gate: any missing agent step (read or mutation) or seq fallback sets canonical_validity=false.

The experimental UNIT is ONE controlled task run == ONE HookJournal DB (streams/agents/epochs within
it are NESTED observations, not independent units). capture_stats and capture_errors are journal-
global and carry no stream key, so per-stream admission is not decidable -- admission is a whole-run
gate (`admission.canonical_admissible`), with a separate `token_share_eligible` gate for token headlines
and a `bash_coverage_clean` flag for the strict coverage subset. See docs/corpus-protocol.md.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Optional

from .classify import (CONFIG_REQUIRED, EDIT_PRECONDITION, EXPLORATION, UNKNOWN, VERIFICATION,
                       classify_reads)
from .normalize import to_events

REPORT_SCHEMA_VERSION = "label-report-0.2.0"   # 0.2.0: run-unit, strict admission, provenance rename
PRIMARY_WINDOW = 16
INF_WINDOW = 10 ** 12                       # effectively unbounded; distances never exceed it
DEFAULT_WINDOWS = (8, 16, 32, INF_WINDOW)
HOOK_SCHEMA_EXPECTED = "0.3.0"

# closure_reason is an ENUM, not free text: arbitrary strings could smuggle identifiers back into an
# artifact that claims aggregate-only privacy.
_CLOSURE_REASONS = frozenset({"controlled_run_completed", "superseded_clear_epoch", "timeout", "aborted"})

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
            reason = m.get("closure_reason")
            if closed and reason is None:
                reason = "controlled_run_completed"              # the common controlled-run default
            if reason is not None and reason not in _CLOSURE_REASONS:
                raise ValueError(f"closure_reason {reason!r} is not one of {sorted(_CLOSURE_REASONS)}; "
                                 "free text is rejected (it could reintroduce identifiers)")
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


def build_report(db_path: str, *, manifest: Optional[dict] = None, windows=None,
                 primary: Optional[int] = None, canonical: bool = True,
                 runtime_commit_sha: Optional[str] = None, client_version: Optional[str] = None,
                 include_stream_map: bool = False) -> dict:
    # CANONICAL mode (the default) LOCKS the preregistration: primary=16, windows={8,16,32,inf}. Any
    # experimentation must pass canonical=False with explicit windows/primary, which stamps
    # canonical_report=false so a tuned window set can never masquerade as the preregistered result.
    if canonical:
        windows, primary = DEFAULT_WINDOWS, PRIMARY_WINDOW
    else:
        windows = tuple(windows) if windows else DEFAULT_WINDOWS
        primary = PRIMARY_WINDOW if primary is None else primary
    if primary not in windows:
        raise ValueError(f"primary window {_win_key(primary)} is not in the window set "
                         f"{[_win_key(w) for w in windows]}; the primary MUST be one of the windows")

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

    def is_fully_observed(eid) -> bool:
        """A read in a CLOSED stream, within its observed region -- a complete case (no censoring)."""
        row = row_by_id[eid]
        cl = closure.get(row["stream_key"], {})
        if not cl.get("closed"):
            return False
        ca = cl.get("closed_at_seq")
        return ca is None or row["seq"] <= ca

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

    # ---- privacy: pseudonymize stream identifiers in the SHAREABLE artifact ----
    # A raw stream_key embeds session/agent lineage (an identifier), so it must not appear in the
    # shared evidence. Deterministic pseudonyms (stream_001..) ordered by first-seen seq; the raw
    # mapping is emitted ONLY when include_stream_map is set (a local/debug artifact).
    min_seq = {}
    for r in rows:
        sk = r["stream_key"]
        if sk:
            min_seq[sk] = min(min_seq.get(sk, r["seq"]), r["seq"])
    order = sorted(closure.keys(), key=lambda sk: (min_seq.get(sk, 0), sk))
    pseudo = {sk: f"stream_{i + 1:03d}" for i, sk in enumerate(order)}
    closure_pub = {pseudo[sk]: v for sk, v in closure.items()}

    # ---- provenance ------------------------------------------------------
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    est_ids = {r["token_estimator_id"] for r in rows if r["token_estimator_id"]}
    with open(db_path, "rb") as fh:
        journal_sha = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    import contextruntime.classify as _clsmod                    # identity of the classifier SOURCE,
    with open(_clsmod.__file__, "rb") as fh:                     # independent of the repo commit
        classifier_blob_sha = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    wk_list = [_win_key(w) for w in windows]
    provenance = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "hook_schema_version": meta.get("hook_schema_version"),
        "canonical_report": canonical,                          # false => experimental windows, do not quote
        "runtime_commit_sha": runtime_commit_sha,               # repo/runtime snapshot (was classifier_sha)
        "classifier_blob_sha": classifier_blob_sha,             # sha256 of classify.py source
        "journal_sha256": journal_sha,
        "client_version": client_version,                       # single stamp -- one DB must be one client
        "token_estimator_id": sorted(est_ids)[0] if len(est_ids) == 1 else sorted(est_ids),
        "primary_window": primary,
        "windows": wk_list,
        "closure_manifest": closure_pub,                        # pseudonymized
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

    # ---- tokens: measurement-honest accounting -------------------------
    # A read is FULLY MEASURED only when token_attribution=='attributed' AND a non-NULL weight AND
    # token_status=='text' (a full text response). 'text_partial_multimodal' carries ONLY the text
    # component's tokens -- the image contribution is unmeasured -- so it stays OUT of the canonical
    # denominator (reported separately, never silently full-counted). 'multimodal'/'unsupported' carry
    # no usable weight; 'ambiguous_multipath' can't be attributed to one path. NULL is never zero cost.
    def _tok_cat(eid) -> str:
        r = row_by_id[eid]
        attr, tok, st = r["token_attribution"], r["model_visible_tokens"], r["token_status"]
        if attr == "ambiguous_multipath":
            return "ambiguous_multipath"
        if attr == "attributed" and tok is not None and st == "text":
            return "fully_attributed_text"
        if st == "text_partial_multimodal":
            return "partial_multimodal"
        if st == "multimodal":
            return "multimodal_unmeasured"
        if st == "unsupported":
            return "unsupported"
        return "unmeasured_other"

    def _tok(eid) -> int:
        return row_by_id[eid]["model_visible_tokens"] or 0

    cat_of = {eid: _tok_cat(eid) for eid in primary_labels}
    breakdown = Counter(cat_of.values())
    fully_ids = {eid for eid, c in cat_of.items() if c == "fully_attributed_text"}
    partial_ids = {eid for eid, c in cat_of.items() if c == "partial_multimodal"}
    total_reads = len(primary_labels)
    event_share = Counter(lab.observed_class for lab in primary_labels.values())
    token_share = Counter()
    for eid in fully_ids:
        token_share[primary_labels[eid].observed_class] += _tok(eid)
    t_all_fully = sum(token_share.values())
    tokens = {
        "note": ("Fully-measured tokens require attributed AND non-NULL AND token_status=='text'. "
                 "text_partial_multimodal (text tokens only, image unmeasured) is kept OUT of the "
                 "denominator but reported; multimodal/ambiguous/unsupported carry no usable weight; "
                 "NULL is never zero cost. estimator=%s" % provenance["token_estimator_id"]),
        "measurement_breakdown": {k: breakdown.get(k, 0) for k in
                                  ("fully_attributed_text", "partial_multimodal", "multimodal_unmeasured",
                                   "ambiguous_multipath", "unsupported", "unmeasured_other")},
        "fully_measured_reads": len(fully_ids),
        "attribution_coverage": _pct(len(fully_ids), total_reads),
        "fully_measured_tokens_total": t_all_fully,
        "partial_multimodal_text_tokens": sum(_tok(eid) for eid in partial_ids),   # OUT of the denom
        "event_share": {c: _pct(event_share.get(c, 0), total_reads) for c in _ALL_CLASSES},
        "token_share_fully_measured": {c: _pct(token_share.get(c, 0), t_all_fully) for c in _ALL_CLASSES},
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

    # ---- agent-step integrity as a VALIDITY GATE ------------------------
    # `step` is the causal unit; `seq` is only deterministic ordering. The live hook path normally
    # supplies step, so any seq fallback in a PRIMARY precondition means the canonical W=16 evidence
    # rested on a non-causal distance -- flag the run non-canonical-valid rather than let it pass
    # silently. (Missing steps may still appear diagnostically; they just can't be canonical.)
    missing_step_reads = sum(1 for e in events if e["kind"] == "read" and e.get("step") is None)
    missing_step_mut = sum(1 for e in events if e["kind"] == "edit" and e.get("step") is None)
    # A missing step can flip an outside_causal_window/UNKNOWN decision even without ever producing a
    # precondition, so canonical validity requires ALL of them zero, not just the precondition path.
    steps_clean = (missing_step_reads == 0 and missing_step_mut == 0 and seq_fallback == 0)
    canonical_valid = canonical and steps_clean
    validity = {
        "canonical_report": canonical,                          # window config is the locked set
        "canonical_validity": canonical_valid,                  # AND every agent step present, no seq fallback
        "missing_step_reads": missing_step_reads,
        "missing_step_mutations": missing_step_mut,
        "seq_fallback_preconditions": seq_fallback,
        "notes": ([] if canonical_valid else
                  ([] if canonical else ["experimental window set -- canonical_report=false"])
                  + ([f"{missing_step_reads} read(s)/{missing_step_mut} mutation(s) missing agent step"]
                     if (missing_step_reads or missing_step_mut) else [])
                  + ([f"{seq_fallback} primary EDIT_PRECONDITION(s) used seq fallback"]
                     if seq_fallback else [])),
    }

    # ---- ADMISSION GATE (exact, mechanical -- the corpus harness applies these booleans) ----
    checks = {
        "pre_equals_batch": integrity["pre_tool_calls_seen"] == integrity["batch_tool_calls_resolved"],
        "zero_errors": integrity["errors"] == 0,
        "zero_pending": integrity["pending_tools"] == 0,
        "hook_schema_expected": provenance["hook_schema_version"] == HOOK_SCHEMA_EXPECTED,
        "canonical_report": canonical,
        "canonical_validity": canonical_valid,
        "provenance_complete": (client_version is not None and runtime_commit_sha is not None),
    }
    admission = {
        "checks": checks,
        "canonical_admissible": all(checks.values()),           # gates EVENT-label analysis
        "bash_coverage_clean": integrity["unknown_bash_calls"] == 0,   # strict subset; retain+count if false
        "token_share_eligible": (len(primary_labels) > 0
                                 and len(fully_ids) == len(primary_labels)),   # separate token headline gate
        "note": ("canonical_admissible gates EVENT-label analysis; token_share_eligible SEPARATELY "
                 "gates the token-share headline (needs 100% fully-measured text reads); "
                 "bash_coverage_clean marks the strict coverage subset -- retain+count runs that fail "
                 "it, stratified by task category, never delete."),
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
        # note reflects the ACTUAL window set, not a hardcoded one -- no preregistration loophole
        "stability_precondition_note": ("|" + " ∩ ".join(f"P{k}" for k in wk_list)
                                        + f"| / |P{_win_key(primary)}|"),
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

    # complete-case (DESCRIPTIVE, over fully-observed streams only -- NOT an unbiased population
    # estimate; open streams are dropped, which is itself a selection): closed reads within closure.
    cc_reads = [eid for eid in primary_labels if is_fully_observed(eid)]
    cc_expl = sum(1 for eid in cc_reads if primary_labels[eid].observed_class == EXPLORATION)
    all_streams_closed = (streams_closed == len(closure) and len(closure) > 0)

    # TOKEN-side censoring, mirroring the event side but over FULLY-MEASURED tokens only (repair: the
    # token result must be right-censored too, else an open stream overstates exploration TOKEN share
    # even after its event headline is censored).
    t_expl_prov = sum(_tok(eid) for eid in fully_ids
                      if primary_labels[eid].observed_class == EXPLORATION and is_provisional(eid))
    t_expl_final = sum(_tok(eid) for eid in fully_ids
                       if primary_labels[eid].observed_class == EXPLORATION and not is_provisional(eid))
    t_cc_ids = [eid for eid in fully_ids if is_fully_observed(eid)]
    t_cc_all = sum(_tok(eid) for eid in t_cc_ids)
    t_cc_expl = sum(_tok(eid) for eid in t_cc_ids if primary_labels[eid].observed_class == EXPLORATION)

    censoring = {
        "principal_result": "censoring_bounds",                 # identification bounds, NOT a sampling CI
        "principal_result_note": ("Until EVERY stream is closed, the exploration share is a BOUND "
                                  "[floor, before], not a point (and NOT a sampling confidence interval). "
                                  "after is the provisional-exploration-excluded reading; complete_case "
                                  "is a DESCRIPTIVE fully-closed-streams-only figure. When all streams "
                                  "close, floor=after=before=complete_case."),
        "all_streams_closed": all_streams_closed,
        "streams_closed": streams_closed,
        "streams_open": len(closure) - streams_closed,
        "provisional_reads_total": prov_total,
        "provisional_by_class": {c: prov_by_class.get(c, 0) for c in _ALL_CLASSES if prov_by_class.get(c)},
        "provisional_exploration_reads_excluded": prov_expl,
        "exploration_events": {
            "before_pct": _pct(expl_all, total_p),
            "after_pct": _pct(final_expl, total_p - prov_expl),
            "floor_pct": _pct(final_expl, total_p),
            "complete_case_pct": _pct(cc_expl, len(cc_reads)),
            "complete_case_reads": len(cc_reads),
            "final_exploration_reads": final_expl,
            "provisional_exploration_reads": prov_expl,
            "denominator_before": total_p,
            "denominator_after": total_p - prov_expl,
        },
        "exploration_tokens": {
            "before_pct": _pct(t_expl_final + t_expl_prov, t_all_fully),
            "after_pct": _pct(t_expl_final, t_all_fully - t_expl_prov),
            "floor_pct": _pct(t_expl_final, t_all_fully),
            "complete_case_pct": _pct(t_cc_expl, t_cc_all),
            "exploration_tokens_final": t_expl_final,
            "exploration_tokens_provisional": t_expl_prov,
            "fully_measured_tokens_total": t_all_fully,
            "note": "over FULLY-MEASURED tokens only; floor <= after <= before by construction.",
        },
        "labels_before": before,
        "labels_after": after,                                       # provisional exploration removed only
        # back-compat scalars (event side); interval fields above are the principal result
        "headline_exploration": {
            "before_pct": _pct(expl_all, total_p), "after_pct": _pct(final_expl, total_p - prov_expl),
            "floor_pct": _pct(final_expl, total_p), "final_exploration_reads": final_expl,
            "provisional_exploration_reads": prov_expl, "denominator_before": total_p,
            "denominator_after": total_p - prov_expl},
        "headline_exploration_pct_before": _pct(expl_all, total_p),
        "headline_exploration_pct_after": _pct(final_expl, total_p - prov_expl),
        "closure_by_stream": closure_pub,                            # pseudonymized (privacy)
    }

    conn.close()
    out = {
        "provenance": provenance,
        "validity": validity,
        "admission": admission,
        "capture_integrity": integrity,
        "labels_primary": labels_primary,
        "tokens": tokens,
        "causal_distance_precondition": causal,
        "window_sensitivity": sensitivity,
        "censoring": censoring,
    }
    if include_stream_map:                                           # local/debug only, never shared
        out["_stream_key_map"] = {v: k for k, v in pseudo.items()}
    return out


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
    va, ad = rep["validity"], rep["admission"]
    A("[1] PROVENANCE + VALIDITY + ADMISSION")
    A(f"    report_schema={p['report_schema_version']}  hook_schema={p['hook_schema_version']}  "
      f"estimator={p['token_estimator_id']}")
    A(f"    runtime_commit={p['runtime_commit_sha']}  classifier_blob={p['classifier_blob_sha'][:19]}...  "
      f"client={p['client_version']}")
    A(f"    journal={p['journal_sha256'][:23]}...  primary_window={p['primary_window']}  "
      f"windows={p['windows']}")
    A(f"    canonical_report={p['canonical_report']}  canonical_validity={va['canonical_validity']}  "
      f"seq_fallback_preconditions={va['seq_fallback_preconditions']}  "
      f"missing_step(reads={va['missing_step_reads']},muts={va['missing_step_mutations']})")
    if va["notes"]:
        A(f"    validity_notes={va['notes']}")
    A(f"    ADMISSION canonical_admissible={ad['canonical_admissible']}  "
      f"token_share_eligible={ad['token_share_eligible']}  bash_coverage_clean={ad['bash_coverage_clean']}")
    A(f"    checks={ {k: v for k, v in ad['checks'].items()} }")
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
    A("[4] TOKENS (fully-measured = attributed + non-NULL + text; NULL != zero cost)")
    A(f"    measurement_breakdown={tk['measurement_breakdown']}")
    A(f"    fully_measured_coverage={tk['attribution_coverage']}% "
      f"(reads={tk['fully_measured_reads']}) fully_measured_tokens={tk['fully_measured_tokens_total']}  "
      f"partial_multimodal_text_tokens={tk['partial_multimodal_text_tokens']} (OUT of denom)")
    A(f"    event_share%={ {k: v for k, v in tk['event_share'].items() if v} }")
    A(f"    token_share_fully_measured%={ {k: v for k, v in tk['token_share_fully_measured'].items() if v} }")
    A("")
    cd = rep["causal_distance_precondition"]
    A(f"[5] CAUSAL DISTANCE for EDIT_PRECONDITION @ W={cd['window']} (agent steps; n={cd['n']})")
    A(f"    p50={cd['p50']} p90={cd['p90']} p95={cd['p95']} max={cd['max']}  "
      f"seq_fallback={cd['seq_fallback_count']}")
    A(f"    histogram={cd['histogram']}")
    A("")
    ws = rep["window_sensitivity"]
    A(f"[6] WINDOW SENSITIVITY {{{','.join(p['windows'])}}} "
      f"(W={p['primary_window']} primary{'' if p['canonical_report'] else ', EXPERIMENTAL'}; others sensitivity)")
    for wk, cc in ws["class_counts_by_window"].items():
        star = " *primary" if wk == str(rep["provenance"]["primary_window"]) else ""
        A(f"    W={wk:<3} {cc}{star}")
    A(f"    labels_invariant_all_windows={ws['labels_invariant_all_windows']}/{ws['labels_total']} "
      f"({ws['invariant_fraction']}%)  stability_precondition={ws['stability_precondition']} "
      f"({ws['stability_precondition_note']})")
    A(f"    transitions_vs_primary={ws['transition_matrices_vs_primary']}")
    A("")
    cn = rep["censoring"]
    ee, et = cn["exploration_events"], cn["exploration_tokens"]
    A("[7] RIGHT-CENSORING -- exploration is an INTERVAL until all streams close "
      f"(all_closed={cn['all_streams_closed']})")
    A(f"    streams: closed={cn['streams_closed']} open={cn['streams_open']}  "
      f"provisional_total={cn['provisional_reads_total']} by_class={cn['provisional_by_class']}")
    A(f"    exploration EVENTS%: FLOOR={ee['floor_pct']} <= AFTER={ee['after_pct']} <= "
      f"BEFORE={ee['before_pct']}   complete_case={ee['complete_case_pct']} (n={ee['complete_case_reads']})")
    A(f"    exploration TOKENS%: FLOOR={et['floor_pct']} <= AFTER={et['after_pct']} <= "
      f"BEFORE={et['before_pct']}   complete_case={et['complete_case_pct']} "
      f"(fully_measured_tok={et['fully_measured_tokens_total']})")
    A("    [only provisional EXPLORATION moves between floor/after/before; verification/config stay]")
    A(_fmt_row("labels BEFORE", cn["labels_before"]["counts"], cn["labels_before"]["percent"]))
    A(_fmt_row("labels AFTER ", cn["labels_after"]["counts"], cn["labels_after"]["percent"]))
    A("=" * 78)
    return "\n".join(L)


def report_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, sort_keys=True, default=str)
