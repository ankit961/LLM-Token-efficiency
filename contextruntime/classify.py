"""Retrospective read classification (design v1.2, Phase 2.4-C) — OBSERVE-ONLY.

Labels each READ event with what LATER EVIDENCE says it was for, so we can measure how much
admitted context was *exploration* (a candidate for semantic reads / admission) vs an *edit
precondition* (must be admitted). This is RETROSPECTIVE ground truth. It is kept strictly
separate from any real-time `predicted_class`: this module never predicts, it only attributes
after the fact — so a classifier's precision/recall can be measured against it, never assumed.

Core rule (deliberately conservative, to keep UNKNOWN honest — we do NOT turn every read before
an edit into a prerequisite). For an edit E of path p in a stream, the EDIT_PRECONDITION is the
single LATEST eligible preceding read of p. A read R(p) is eligible iff:

    same stream · same path · R before E · R after the previous mutation of p ·
    R's content version still applies at E · E within the causal window of R

Only that read is EDIT_PRECONDITION; earlier reads of p stay EXPLORATION / VERIFICATION / UNKNOWN.
A read that WAS about p but whose content version no longer applied at E is UNKNOWN (stale) —
never silently counted as exploration.

Evidence honesty: the temporal-causal inference here is *retrospectively attributed*, grade B; a
weaker heuristic (e.g. a config path) is grade C. We only ever produce
`classification_source=client_tracker_confirmed` (grade A) once the client's edit-read requirement
is MECHANICALLY observable — not implemented yet, so the denominator is never called "exact."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

# --- labels (internal 5-way; the public enforcement metric collapses to exploration vs precondition)
EXPLORATION = "exploration"
EDIT_PRECONDITION = "edit_precondition"
VERIFICATION = "verification"
CONFIG_REQUIRED = "config_required"
UNKNOWN = "unknown"

# --- how the label was established (SEPARATE from the label; drives the evidence grade)
CLIENT_TRACKER_CONFIRMED = "client_tracker_confirmed"   # mechanically observed edit-read requirement (future -> A)
TEMPORAL_CAUSAL = "temporal_causal"                     # read -> edit-same-path inference (-> B)
HEURISTIC = "heuristic"                                 # weaker signal, e.g. config path (-> C)

# reads that reach the model WITHOUT SemanticFS — the bypass channels 2.4 measures against
NATIVE_CHANNELS = ("native_read", "bash_materialization")


@dataclass
class Label:
    observed_class: str
    classification_source: str
    evidence_grade: str                    # A | B | C
    edit_event_id: Optional[str] = None    # the edit this read is a precondition for (if any)


def _is_read(e) -> bool:
    return e.get("kind") == "read"


def _is_edit(e) -> bool:
    return e.get("kind") == "edit"


def classify_reads(events, *, window: int = 50,
                   config_matcher: Optional[Callable[[str], bool]] = None) -> dict:
    """Return ``{event_id: Label}`` for every READ event. Events are dicts with at least
    ``event_id``, ``seq``, ``stream_key``, ``kind`` (``read``|``edit``), ``path``; reads may carry
    ``content_version`` and ``channel``. Pure and order-independent (sorted by ``seq``)."""
    evs = sorted(events, key=lambda e: e["seq"])
    labels: dict = {e["event_id"]: Label(EXPLORATION, TEMPORAL_CAUSAL, "B")
                    for e in evs if _is_read(e)}

    # PRECONDITION pass: for each edit, the latest eligible preceding read of the same path.
    for i, E in enumerate(evs):
        if not _is_edit(E):
            continue
        s, p = E.get("stream_key"), E.get("path")
        prior = evs[:i]
        # previous mutation of p in this stream = exclusive lower bound for eligibility
        prev_mut = max((F["seq"] for F in prior
                        if _is_edit(F) and F.get("stream_key") == s and F.get("path") == p),
                       default=None)
        best = None
        for R in prior:
            if not _is_read(R) or R.get("stream_key") != s or R.get("path") != p:
                continue
            if prev_mut is not None and R["seq"] <= prev_mut:      # must be after the last mutation
                continue
            if E["seq"] - R["seq"] > window:                      # within the causal window
                continue
            if best is None or R["seq"] > best["seq"]:            # LATEST eligible, not every prior read
                best = R
        if best is None:
            continue
        rv, ev = best.get("content_version"), E.get("content_version")
        if rv is not None and ev is not None and rv != ev:
            # the read was about p but its version no longer applied at the edit — stale, not a
            # precondition and NOT silently exploration.
            labels[best["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C")
            continue
        grade = "B" if (rv is not None and ev is not None) else "C"   # unconfirmed version -> weaker
        labels[best["event_id"]] = Label(EDIT_PRECONDITION, TEMPORAL_CAUSAL, grade,
                                         edit_event_id=E["event_id"])

    # SECONDARY pass over reads still EXPLORATION: verification (re-read after an edit of the same
    # path) or config_required (heuristic on the path).
    for i, R in enumerate(evs):
        if not _is_read(R) or labels[R["event_id"]].observed_class != EXPLORATION:
            continue
        s, p = R.get("stream_key"), R.get("path")
        verifies = any(_is_edit(F) and F.get("stream_key") == s and F.get("path") == p
                       and 0 < R["seq"] - F["seq"] <= window for F in evs[:i])
        if verifies:
            labels[R["event_id"]] = Label(VERIFICATION, TEMPORAL_CAUSAL, "B")
        elif config_matcher and p and config_matcher(p):
            labels[R["event_id"]] = Label(CONFIG_REQUIRED, HEURISTIC, "C")

    return labels


def exploration_bypass(events, labels, *, native_channels=NATIVE_CHANNELS) -> dict:
    """ExplorationBypassRate reported by EVENTS *and* by TOKENS: of all exploration reads, what
    share went through a NATIVE channel (bypassing SemanticFS)? Both, because a 10-token native
    read and a 20,000-token `cat` must not weigh equally. Token weight uses the full
    transport_content_tokens (falls back to semantic_payload_tokens)."""
    by_id = {e["event_id"]: e for e in events}
    expl = [by_id[eid] for eid, lab in labels.items()
            if lab.observed_class == EXPLORATION and eid in by_id]
    if not expl:
        return {"events": None, "tokens": None, "n_exploration": 0}

    def toks(e) -> int:
        return e.get("transport_content_tokens") or e.get("semantic_payload_tokens") or 0

    native = [e for e in expl if e.get("channel") in native_channels]
    native_tok = sum(toks(e) for e in native)
    all_tok = sum(toks(e) for e in expl)
    return {"events": len(native) / len(expl),
            "tokens": (native_tok / all_tok) if all_tok else None,
            "n_exploration": len(expl),
            "n_native": len(native)}
