"""Phase 2.4-C — retrospective read classification (observe-only).

Synthetic event sequences that pin the label semantics: latest-eligible-same-path attribution
(NOT every prior read), mutation boundaries, content-version staleness -> UNKNOWN, verification,
cross-stream isolation, the causal window, config heuristic, and exploration-bypass by events
AND tokens. The labeller is retrospective/observed only — never a real-time prediction.
"""
from contextruntime.classify import (CONFIG_REQUIRED, EDIT_PRECONDITION, EXPLORATION, HEURISTIC,
                                     TEMPORAL_CAUSAL, UNKNOWN, VERIFICATION, classify_reads,
                                     exploration_bypass)


def _r(eid, seq, path, stream="s", cv=None, channel="native_read", tokens=None):
    e = {"event_id": eid, "seq": seq, "kind": "read", "stream_key": stream, "path": path,
         "content_version": cv, "channel": channel}
    if tokens is not None:
        e["transport_content_tokens"] = tokens
    return e


def _e(eid, seq, path, stream="s", cv=None):
    return {"event_id": eid, "seq": seq, "kind": "edit", "stream_key": stream, "path": path,
            "content_version": cv}


# The core rule: only the LATEST eligible read before an edit is the precondition — earlier
# reads of the same path stay exploration (or we'd systematically undercount exploration).
def test_latest_eligible_is_the_only_precondition():
    labels = classify_reads([_r("r1", 1, "a.py"), _r("r2", 3, "a.py"), _e("e", 5, "a.py")])
    assert labels["r2"].observed_class == EDIT_PRECONDITION and labels["r2"].edit_event_id == "e"
    assert labels["r1"].observed_class == EXPLORATION            # NOT a prerequisite


# A read only counts for the edit that follows it after the previous mutation of that path.
def test_mutation_boundary_scopes_the_precondition():
    labels = classify_reads([_r("r0", 1, "a.py"), _e("e0", 2, "a.py"),
                             _r("r1", 3, "a.py"), _e("e1", 4, "a.py")])
    assert labels["r0"].edit_event_id == "e0"                    # r0 precedes e0
    assert labels["r1"].edit_event_id == "e1"                    # r1 is after e0 -> belongs to e1
    assert labels["r1"].observed_class == EDIT_PRECONDITION


# A read whose content version no longer applied at the edit is UNKNOWN (stale), not exploration.
def test_stale_read_is_unknown_not_exploration():
    labels = classify_reads([_r("r", 1, "a.py", cv="v1"), _e("e", 3, "a.py", cv="v2")])
    assert labels["r"].observed_class == UNKNOWN
    assert labels["r"].evidence_grade == "C"


def test_applicable_version_is_grade_b_missing_is_grade_c():
    good = classify_reads([_r("r", 1, "a.py", cv="v1"), _e("e", 3, "a.py", cv="v1")])
    assert good["r"].observed_class == EDIT_PRECONDITION and good["r"].evidence_grade == "B"
    unk = classify_reads([_r("r", 1, "a.py"), _e("e", 3, "a.py")])   # no content_version either side
    assert unk["r"].observed_class == EDIT_PRECONDITION and unk["r"].evidence_grade == "C"


def test_read_after_edit_is_verification():
    labels = classify_reads([_e("e", 1, "a.py"), _r("r", 2, "a.py")])
    assert labels["r"].observed_class == VERIFICATION


def test_read_never_edited_is_exploration():
    labels = classify_reads([_r("r", 1, "a.py")])
    assert labels["r"].observed_class == EXPLORATION and labels["r"].evidence_grade == "B"


def test_causal_window_excludes_distant_read():
    labels = classify_reads([_r("r", 1, "a.py"), _e("e", 100, "a.py")], window=5)
    assert labels["r"].observed_class == EXPLORATION            # too far before the edit


def test_cross_stream_isolation():
    labels = classify_reads([_r("r", 1, "a.py", stream="A"), _e("e", 2, "a.py", stream="B")])
    assert labels["r"].observed_class == EXPLORATION            # edit is in a different stream


def test_config_required_heuristic():
    labels = classify_reads([_r("r", 1, "config.yaml")],
                            config_matcher=lambda p: p.endswith((".yaml", ".toml", ".ini")))
    assert labels["r"].observed_class == CONFIG_REQUIRED
    assert labels["r"].classification_source == HEURISTIC and labels["r"].evidence_grade == "C"


def test_only_reads_are_labelled_and_never_grade_a_yet():
    events = [_r("r", 1, "a.py"), _e("e", 2, "a.py")]
    labels = classify_reads(events)
    assert "e" not in labels                                    # edits are not read events
    # no client-tracker confirmation exists yet -> nothing is grade A / "exact"
    assert all(lab.evidence_grade in ("B", "C") for lab in labels.values())
    assert all(lab.classification_source in (TEMPORAL_CAUSAL, HEURISTIC) for lab in labels.values())


# Exploration bypass must be reported BOTH ways: a 10-token native read and a 200-token semantic
# read are one event each, but very different token weight.
def test_exploration_bypass_events_and_tokens():
    events = [
        _r("n", 1, "a.py", channel="bash_materialization", tokens=10),   # native exploration
        _r("s", 2, "b.py", channel="semanticfs", tokens=200),            # semantic exploration
    ]
    labels = classify_reads(events)
    bp = exploration_bypass(events, labels)
    assert bp["n_exploration"] == 2 and bp["n_native"] == 1
    assert bp["events"] == 0.5                                  # 1 of 2 reads was native
    assert abs(bp["tokens"] - 10 / 210) < 1e-9                  # but only ~5% of the tokens
