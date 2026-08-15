"""Retrospective read classification (design v1.2, Phase 2.4-C) -- OBSERVE-ONLY.

Labels each READ event with what LATER EVIDENCE says it was for, so admitted context can be
measured as *exploration* (a semantic-reads / admission candidate) vs an *edit precondition*
(must be admitted). Retrospective/observed ONLY -- never a real-time prediction, so a future
classifier's precision/recall can be measured against it.

Semantics (locked with review; conservative, to keep UNKNOWN honest). For a read R of path p:
its relevant mutation is the FIRST edit of p after R (its mutation boundary). Then:

    version(R) known and != the edit's pre-version   -> UNKNOWN  (content_version_conflict, B)
    causal distance(R, edit) > window                -> UNKNOWN  (outside_causal_window)
    otherwise                                        -> eligible candidate for that edit
    the LATEST eligible candidate                    -> EDIT_PRECONDITION
    earlier eligible candidates                      -> UNKNOWN  (superseded; not exploration)
    no future mutation of p at all                   -> EXPLORATION (or VERIFICATION / CONFIG)

A same-path edit OUTSIDE the window becomes UNKNOWN, never EXPLORATION -- so the window controls
how much evidence we are willing to call causal, it does not manufacture exploration.

Distance: the CAUSAL window is measured in AGENT STEPS (`step`), not raw event sequence. `seq`
counts whatever we happen to instrument (adding telemetry channels would change seq-distances
without changing agent behavior), so `seq` is used only for ordering + deterministic tiebreak.
No hard wall-clock cutoff (a developer can pause and resume the same reasoning chain); record
elapsed time for sensitivity analysis instead. Report sensitivity over window in {8, 16, 32, inf}.

Evidence honesty: `client_tracker_confirmed` (grade A) is produced ONLY when the client's
edit-read requirement is mechanically observable (not implemented -> no A labels, denominator
never "exact"). `content_version_conflict` and `temporal_causal` are grade B; `heuristic` is C.
`config_required` is a grade-C role hint from a filename heuristic -- NOT high-confidence ground
truth. Every label carries an auditable `evidence` dict so each number is traceable.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

# --- labels (internal 5-way; the public metric collapses to exploration vs precondition)
EXPLORATION = "exploration"
EDIT_PRECONDITION = "edit_precondition"
VERIFICATION = "verification"
CONFIG_REQUIRED = "config_required"
UNKNOWN = "unknown"

# --- how the label was established (SEPARATE from the label; drives the evidence grade)
CLIENT_TRACKER_CONFIRMED = "client_tracker_confirmed"   # mechanically observed requirement -> A (future)
CONTENT_VERSION_CONFLICT = "content_version_conflict"   # read version != edited version    -> B
TEMPORAL_CAUSAL = "temporal_causal"                     # read -> edit-same-path inference   -> B
HEURISTIC = "heuristic"                                 # weaker signal, e.g. config path    -> C

NATIVE_CHANNELS = ("native_read", "bash_materialization")
DEFAULT_WINDOW = 16                # agent steps (provisional; report sensitivity over {8,16,32,inf})


@dataclass
class Label:
    observed_class: str
    classification_source: str
    evidence_grade: str                    # A | B | C
    edit_event_id: Optional[str] = None    # the edit this read is a precondition for (if any)
    reason: Optional[str] = None
    evidence: dict = field(default_factory=dict)   # auditable explanation (versions, distance, window)


def _is_read(e) -> bool:
    return e.get("kind") == "read"


def _is_edit(e) -> bool:
    return e.get("kind") == "edit"


def _distance(a, b, key):
    """Causal distance in agent steps when available, else the seq fallback (diagnostic).
    Returns (distance, metric_used)."""
    if a.get(key) is not None and b.get(key) is not None:
        return abs(a[key] - b[key]), key
    return abs(a["seq"] - b["seq"]), "seq"


def classify_reads(events, *, window: int = DEFAULT_WINDOW, distance_key: str = "step",
                   config_matcher: Optional[Callable[[str], bool]] = None) -> dict:
    """Return ``{event_id: Label}`` for every READ event. Events are dicts with at least
    ``event_id``, ``seq``, ``stream_key``, ``kind`` (``read``|``edit``), ``path``; reads/edits may
    carry ``content_version`` (for edits, the version JUST BEFORE the edit) and ``step`` (agent-step
    index); reads may carry ``channel``. Pure and order-independent (sorted by ``seq``)."""
    evs = sorted(events, key=lambda e: e["seq"])
    labels: dict = {}
    reads_by, edits_by = defaultdict(list), defaultdict(list)
    for e in evs:
        key = (e.get("stream_key"), e.get("path"))
        if _is_read(e):
            reads_by[key].append(e)
        elif _is_edit(e):
            edits_by[key].append(e)

    candidates: dict = defaultdict(list)         # edit_event_id -> [(read, grade, evidence)]
    for key, reads in reads_by.items():
        edits = edits_by.get(key, [])            # sorted by seq (evs is sorted)
        for R in reads:
            # A SEARCH / PATH-LISTING / DERIVED (piped-summarized) materialization shows the model
            # search results, a directory listing, or a transformed artifact -- NOT a specific file's
            # pre-edit state. It can never be a file edit_precondition; but we ALSO cannot mechanically
            # prove its role was exploration (a repo-wide grep may have surfaced the exact edit target).
            # The honest observed label is UNKNOWN until result-path evidence resolves the role -- so it
            # never enters the exploration headline denominator by fiat.
            if R.get("representation") in ("search", "path_listing", "derived"):
                labels[R["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                              reason="non_file_materialization_role_unresolved")
                continue
            # A read-time RACE (pre-hash != post-hash) means we don't know which state the model
            # actually consumed -> UNKNOWN, not a grade-C precondition candidate.
            if R.get("version_status") == "raced":
                labels[R["event_id"]] = Label(UNKNOWN, CONTENT_VERSION_CONFLICT, "B",
                                              reason="read_version_race")
                continue
            future = [e for e in edits if e["seq"] > R["seq"]]
            if not future:
                labels[R["event_id"]] = Label(EXPLORATION, TEMPORAL_CAUSAL, "B",
                                              reason="no_future_mutation")
                continue
            E = future[0]                        # first edit of p after R = its mutation boundary
            # An UNVERIFIED mutation (a hash was unavailable, so we can't confirm bytes changed) is
            # an UNCERTAINTY boundary: a read whose causal story crosses it is UNKNOWN, never a
            # precondition -- we are not entitled to assert the edit happened at that version.
            if E.get("mutation_status") == "unverified":
                labels[R["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                              reason="unverified_mutation_boundary",
                                              evidence={"target_mutation_id": E["event_id"]})
                continue
            # A read and its candidate edit in the SAME parallel batch have no established causal
            # order (seq is just serialization) -> UNKNOWN, never a manufactured Read->Edit.
            if R.get("batch_id") is not None and R.get("batch_id") == E.get("batch_id"):
                labels[R["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                              reason="parallel_order_ambiguous",
                                              evidence={"target_mutation_id": E["event_id"],
                                                        "batch_id": R.get("batch_id")})
                continue
            dist, metric = _distance(R, E, distance_key)
            rv, ev = R.get("content_version"), E.get("content_version")   # ev = edit's PRE-version
            evi = {"target_mutation_id": E["event_id"], "read_content_version": rv,
                   "mutation_pre_version": ev, "version_match": (rv is not None and rv == ev),
                   "distance": {metric: dist}, "window": {"metric": distance_key, "threshold": window}}
            if rv is not None and ev is not None and rv != ev:
                labels[R["event_id"]] = Label(UNKNOWN, CONTENT_VERSION_CONFLICT, "B",
                                              reason="content_version_conflict", evidence=evi)
            elif dist > window:
                labels[R["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                              reason="outside_causal_window", evidence=evi)
            else:
                grade = "B" if (rv is not None and ev is not None) else "C"
                candidates[E["event_id"]].append((R, grade, evi))
    # Latest eligible candidate is the precondition; EARLIER eligible reads are UNKNOWN (not
    # exploration) -- conservative, so we never inflate the exploration denominator.
    for eid, cands in candidates.items():
        cands.sort(key=lambda c: (c[0]["seq"], c[0]["event_id"]))   # deterministic tiebreak
        R, grade, evi = cands[-1]
        labels[R["event_id"]] = Label(EDIT_PRECONDITION, TEMPORAL_CAUSAL, grade,
                                      edit_event_id=eid, reason="latest_eligible", evidence=evi)
        for R2, _g, evi2 in cands[:-1]:
            labels[R2["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                           reason="superseded_by_later_eligible_read", evidence=evi2)

    # Secondary pass over reads with no future mutation of their path: a re-read within the window
    # after an edit of that path is VERIFICATION; a config path is a grade-C role hint.
    for i, R in enumerate(evs):
        if not _is_read(R) or labels[R["event_id"]].observed_class != EXPLORATION:
            continue
        s, p = R.get("stream_key"), R.get("path")
        prior_edit = next((F for F in reversed(evs[:i])
                           if _is_edit(F) and F.get("stream_key") == s and F.get("path") == p), None)
        if prior_edit is not None and _distance(R, prior_edit, distance_key)[0] <= window:
            if prior_edit.get("mutation_status") == "unverified":
                # the prior mutation isn't confirmed, so we can't call this a verification of it
                labels[R["event_id"]] = Label(UNKNOWN, TEMPORAL_CAUSAL, "C",
                                              reason="prior_unverified_mutation")
            else:
                labels[R["event_id"]] = Label(VERIFICATION, TEMPORAL_CAUSAL, "B",
                                              reason="post_edit_reread")
        elif config_matcher and p and config_matcher(p):
            labels[R["event_id"]] = Label(CONFIG_REQUIRED, HEURISTIC, "C",
                                          reason="config_path_heuristic")
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
        return {"events": None, "tokens": None, "n_exploration": 0, "n_native": 0}

    def toks(e) -> int:
        return e.get("transport_content_tokens") or e.get("semantic_payload_tokens") or 0

    native = [e for e in expl if e.get("channel") in native_channels]
    native_tok = sum(toks(e) for e in native)
    all_tok = sum(toks(e) for e in expl)
    return {"events": len(native) / len(expl),
            "tokens": (native_tok / all_tok) if all_tok else None,
            "n_exploration": len(expl),
            "n_native": len(native)}
