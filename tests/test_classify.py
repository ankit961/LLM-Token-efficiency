"""Phase 2.4-C -- retrospective read classification (observe-only), semantics locked with review.

Pins: latest-eligible attribution (earlier eligible -> UNKNOWN, not exploration), outside-window
future edit -> UNKNOWN (never exploration), content_version_conflict (grade B), agent-step causal
distance (seq only for ordering), verification, config as grade-C role hint, auditable evidence,
and exploration-bypass by events AND tokens.
"""
from contextruntime.classify import (CONFIG_REQUIRED, CONTENT_VERSION_CONFLICT, EDIT_PRECONDITION,
                                     EXPLORATION, HEURISTIC, TEMPORAL_CAUSAL, UNKNOWN, VERIFICATION,
                                     classify_reads, exploration_bypass)


def _r(eid, seq, path, stream="s", cv=None, channel="native_read", tokens=None, step=None):
    e = {"event_id": eid, "seq": seq, "kind": "read", "stream_key": stream, "path": path,
         "content_version": cv, "channel": channel, "step": step}
    if tokens is not None:
        e["transport_content_tokens"] = tokens
    return e


def _e(eid, seq, path, stream="s", cv=None, step=None):
    return {"event_id": eid, "seq": seq, "kind": "edit", "stream_key": stream, "path": path,
            "content_version": cv, "step": step}


# Only the LATEST eligible read is the precondition; the earlier one is UNKNOWN (not exploration).
def test_latest_eligible_precondition_earlier_is_unknown():
    labels = classify_reads([_r("r1", 1, "a.py"), _r("r2", 3, "a.py"), _e("e", 5, "a.py")])
    assert labels["r2"].observed_class == EDIT_PRECONDITION and labels["r2"].edit_event_id == "e"
    assert labels["r1"].observed_class == UNKNOWN                 # earlier eligible, NOT exploration
    assert labels["r1"].reason == "superseded_by_later_eligible_read"


# The key statistical fix: a same-path edit OUTSIDE the window is UNKNOWN, never exploration.
def test_outside_window_future_edit_is_unknown_not_exploration():
    labels = classify_reads([_r("r", 1, "a.py"), _e("e", 100, "a.py")], window=5)
    assert labels["r"].observed_class == UNKNOWN
    assert labels["r"].reason == "outside_causal_window"


# A stale read is UNKNOWN with the content_version_conflict source at grade B (not merely temporal).
def test_stale_read_is_content_version_conflict_grade_b():
    labels = classify_reads([_r("r", 1, "a.py", cv="v1"), _e("e", 3, "a.py", cv="v2")])
    assert labels["r"].observed_class == UNKNOWN
    assert labels["r"].classification_source == CONTENT_VERSION_CONFLICT
    assert labels["r"].evidence_grade == "B" and labels["r"].evidence["version_match"] is False


def test_earlier_stale_read_is_unknown():
    labels = classify_reads([_r("r1", 1, "a.py", cv="v1"),      # stale
                             _r("r2", 2, "a.py", cv="v2"),      # fresh precondition
                             _e("e", 3, "a.py", cv="v2")])
    assert labels["r1"].observed_class == UNKNOWN and labels["r1"].classification_source == CONTENT_VERSION_CONFLICT
    assert labels["r2"].observed_class == EDIT_PRECONDITION


# The causal window is measured in AGENT STEPS; seq is only ordering. A read far away in seq but
# close in agent steps is IN window; close in seq but far in steps is OUT.
def test_window_uses_agent_step_distance_not_seq():
    inw = classify_reads([_r("r", 1, "a.py", step=10), _e("e", 900, "a.py", step=12)], window=5)
    assert inw["r"].observed_class == EDIT_PRECONDITION          # seq far (899) but 2 agent steps
    outw = classify_reads([_r("r", 1, "a.py", step=1), _e("e", 3, "a.py", step=40)], window=5)
    assert outw["r"].observed_class == UNKNOWN                   # seq near (2) but 39 agent steps
    assert outw["r"].evidence["window"]["metric"] == "step"


def test_applicable_version_grade_b_missing_grade_c():
    good = classify_reads([_r("r", 1, "a.py", cv="v1"), _e("e", 3, "a.py", cv="v1")])
    assert good["r"].observed_class == EDIT_PRECONDITION and good["r"].evidence_grade == "B"
    unk = classify_reads([_r("r", 1, "a.py"), _e("e", 3, "a.py")])
    assert unk["r"].observed_class == EDIT_PRECONDITION and unk["r"].evidence_grade == "C"


def test_read_after_edit_is_verification():
    labels = classify_reads([_e("e", 1, "a.py"), _r("r", 2, "a.py")])
    assert labels["r"].observed_class == VERIFICATION


# ...but a re-read after an UNVERIFIED edit cannot be a verification OF that edit -- we never
# confirmed the bytes changed, so calling it grade-B VERIFICATION would overclaim. It is UNKNOWN.
def test_read_after_unverified_edit_is_unknown_not_verification():
    edit = {"event_id": "e", "seq": 1, "kind": "edit", "stream_key": "s", "path": "a.py",
            "content_version": None, "step": None, "mutation_status": "unverified"}
    labels = classify_reads([edit, _r("r", 2, "a.py")])
    assert labels["r"].observed_class == UNKNOWN
    assert labels["r"].reason == "prior_unverified_mutation" and labels["r"].evidence_grade == "C"
    # a VERIFIED prior edit, by contrast, IS a verification
    edit["mutation_status"] = "verified_change"
    assert classify_reads([edit, _r("r", 2, "a.py")])["r"].observed_class == VERIFICATION


def test_read_never_edited_is_exploration():
    labels = classify_reads([_r("r", 1, "a.py")])
    assert labels["r"].observed_class == EXPLORATION and labels["r"].reason == "no_future_mutation"


def test_cross_stream_isolation():
    labels = classify_reads([_r("r", 1, "a.py", stream="A"), _e("e", 2, "a.py", stream="B")])
    assert labels["r"].observed_class == EXPLORATION            # edit in a different stream


# config_required is a grade-C role hint from a filename heuristic, not high-confidence truth.
def test_config_required_is_grade_c_role_hint():
    labels = classify_reads([_r("r", 1, "config.yaml")],
                            config_matcher=lambda p: p.endswith((".yaml", ".toml", ".ini")))
    assert labels["r"].observed_class == CONFIG_REQUIRED
    assert labels["r"].classification_source == HEURISTIC and labels["r"].evidence_grade == "C"


# Every label carries an auditable evidence trail so each number in the paper is traceable.
def test_precondition_carries_audit_evidence():
    labels = classify_reads([_r("r", 1, "a.py", cv="v1", step=2), _e("e", 3, "a.py", cv="v1", step=4)])
    ev = labels["r"].evidence
    assert ev["target_mutation_id"] == "e" and ev["version_match"] is True
    assert ev["distance"]["step"] == 2 and ev["window"]["threshold"] > 0


def test_tiebreak_is_deterministic_under_equal_seq():
    fwd = classify_reads([_r("ra", 2, "a.py"), _r("rb", 2, "a.py"), _e("e", 3, "a.py")])
    rev = classify_reads([_r("rb", 2, "a.py"), _r("ra", 2, "a.py"), _e("e", 3, "a.py")])
    winner = [eid for eid, lab in fwd.items() if lab.observed_class == EDIT_PRECONDITION]
    assert winner == [eid for eid, lab in rev.items() if lab.observed_class == EDIT_PRECONDITION]
    assert len(winner) == 1


def test_no_grade_a_yet_and_edits_not_labelled():
    labels = classify_reads([_r("r", 1, "a.py"), _e("e", 2, "a.py")])
    assert "e" not in labels                                    # edits are not read events
    assert all(lab.evidence_grade in ("B", "C") for lab in labels.values())   # no A signal exists


# A read-time race (pre-hash != post-hash captured live by hooks) -> UNKNOWN, not a candidate.
def test_read_time_race_is_unknown():
    r = {"event_id": "r", "seq": 1, "kind": "read", "stream_key": "s", "path": "a.py",
         "version_status": "raced"}
    labels = classify_reads([r, _e("e", 3, "a.py")])
    assert labels["r"].observed_class == UNKNOWN
    assert labels["r"].reason == "read_version_race" and labels["r"].evidence_grade == "B"


# A read and its candidate edit in the SAME parallel batch have no established order -> UNKNOWN.
def test_parallel_batch_read_edit_is_unknown():
    r = {"event_id": "r", "seq": 1, "kind": "read", "stream_key": "s", "path": "a.py", "batch_id": "b1"}
    e = {"event_id": "e", "seq": 2, "kind": "edit", "stream_key": "s", "path": "a.py", "batch_id": "b1"}
    labels = classify_reads([r, e])
    assert labels["r"].observed_class == UNKNOWN and labels["r"].reason == "parallel_order_ambiguous"


def test_exploration_bypass_events_and_tokens():
    events = [_r("n", 1, "a.py", channel="bash_materialization", tokens=10),
              _r("s", 2, "b.py", channel="semanticfs", tokens=200)]
    labels = classify_reads(events)
    bp = exploration_bypass(events, labels)
    assert bp["n_exploration"] == 2 and bp["n_native"] == 1
    assert bp["events"] == 0.5 and abs(bp["tokens"] - 10 / 210) < 1e-9
