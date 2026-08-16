"""Offline opportunity-ceiling analysis -- zero-cost arithmetic over frozen, already-classified
Observation Corpus journals. Verifies the bucket taxonomy stays conservative (an UNKNOWN read is
never optimistically folded into a reducible bucket) and that only fully-measured tokens are summed.
"""
import json
import sqlite3

from contextruntime.hookjournal import HookJournal

from corpus.opportunity_ceiling import _bucket_of, _tok_cat, aggregate, analyze_journal, compute_robustness

_SENTINEL = object()


def _ev(j, *, eid, kind, step, stream, path, tok=None, tok_attr=None, tstatus=_SENTINEL,
       representation="file", mut=None):
    tstat = ("text" if tok_attr == "attributed" else None) if tstatus is _SENTINEL else tstatus
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": stream,
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": eid, "tool_name": "Read", "kind": kind,
        "channel": "native_read" if kind == "read" else "edit",
        "mutation_source": None, "mutation_status": mut, "representation": representation,
        "path_absolute": path, "path_normalized": path, "repo_relative": None, "repo_id": None,
        "pre_version": None, "post_version": None, "content_version": None, "version_status": "stable",
        "response_hash": None, "model_visible_chars": None, "model_visible_tokens": tok,
        "token_status": tstat, "token_attribution": tok_attr,
        "token_estimator_id": "chars4-v1", "success": 1, "outcome": "success",
        "wall_time_ns": None, "schema_version": "0.3.0"})


def _mk(tmp_path, name, fn):
    db = str(tmp_path / name)
    j = HookJournal(db)
    fn(j)
    j.commit(); j.close()
    return db


# --------------------------------------------------------------------------- pure functions
def test_tok_cat_matches_labelreport_semantics():
    """Regression: an earlier version checked token_status for the CATEGORY names
    ('partial_multimodal'/'multimodal_unmeasured') as if they were raw journal values -- the real
    raw values are 'text_partial_multimodal'/'multimodal'. The bug was latent (this corpus has zero
    such rows) but would have silently misrouted any real multimodal row to 'unmeasured_other'.
    Every case here uses the ACTUAL raw token_status strings the journal writes."""
    assert _tok_cat({"token_attribution": "attributed", "model_visible_tokens": 40,
                     "token_status": "text"}) == "fully_attributed_text"
    assert _tok_cat({"token_attribution": "ambiguous_composite", "model_visible_tokens": 40,
                     "token_status": "text"}) == "ambiguous_composite"
    assert _tok_cat({"token_attribution": "ambiguous_multipath", "model_visible_tokens": 40,
                     "token_status": "text"}) == "ambiguous_multipath"
    assert _tok_cat({"token_attribution": "attributed", "model_visible_tokens": None,
                     "token_status": "text"}) == "unmeasured_other"          # NULL weight never zero-cost
    # raw value 'text_partial_multimodal' -> category 'partial_multimodal'
    assert _tok_cat({"token_attribution": "attributed", "model_visible_tokens": 5,
                     "token_status": "text_partial_multimodal"}) == "partial_multimodal"
    # raw value 'multimodal' -> category 'multimodal_unmeasured'
    assert _tok_cat({"token_attribution": None, "model_visible_tokens": None,
                     "token_status": "multimodal"}) == "multimodal_unmeasured"
    # raw value 'unsupported' -> category 'unsupported' (the one case where raw == category)
    assert _tok_cat({"token_attribution": None, "model_visible_tokens": None,
                     "token_status": "unsupported"}) == "unsupported"
    # the CATEGORY names themselves are never valid raw token_status values -- must NOT match
    assert _tok_cat({"token_attribution": "attributed", "model_visible_tokens": 5,
                     "token_status": "partial_multimodal"}) == "unmeasured_other"
    assert _tok_cat({"token_attribution": "attributed", "model_visible_tokens": 5,
                     "token_status": "multimodal_unmeasured"}) == "unmeasured_other"


def test_bucket_of_is_conservative_never_upgrades_unknown():
    from contextruntime.classify import Label, EDIT_PRECONDITION, EXPLORATION, UNKNOWN, VERIFICATION, CONFIG_REQUIRED
    assert _bucket_of(Label(EDIT_PRECONDITION, "temporal_causal", "B", reason="latest_eligible")) == "required"
    assert _bucket_of(Label(VERIFICATION, "temporal_causal", "B", reason="post_edit_reread")) == "verification"
    assert _bucket_of(Label(EXPLORATION, "temporal_causal", "B", reason="no_future_mutation")) == "exploration_reducible"
    assert _bucket_of(Label(CONFIG_REQUIRED, "heuristic", "C", reason="config_path_heuristic")) == "config_required"
    assert _bucket_of(Label(UNKNOWN, "temporal_causal", "C",
                            reason="non_file_materialization_role_unresolved")) == "search_listing_reducible"
    # every OTHER unknown reason stays unresolved -- never counted as reducible
    for reason in ("outside_causal_window", "content_version_conflict", "read_version_race",
                  "superseded_by_later_eligible_read", "unverified_mutation_boundary",
                  "parallel_order_ambiguous", "prior_unverified_mutation"):
        assert _bucket_of(Label(UNKNOWN, "temporal_causal", "C", reason=reason)) == "unresolved_other"


# --------------------------------------------------------------------------- analyze_journal
def test_analyze_journal_buckets_and_sums_tokens_correctly(tmp_path):
    def build(j):
        # edit_precondition: read a.py then edit a.py, same step-window
        _ev(j, eid="r1", kind="read", step=0, stream="s1", path="/a.py", tok=100, tok_attr="attributed")
        _ev(j, eid="e1", kind="edit", step=1, stream="s1", path="/a.py", mut="verified_change")
        # verification: re-read a.py after the edit
        _ev(j, eid="r2", kind="read", step=2, stream="s1", path="/a.py", tok=30, tok_attr="attributed")
        # exploration: read b.py, never edited
        _ev(j, eid="r3", kind="read", step=0, stream="s1", path="/b.py", tok=50, tok_attr="attributed")
        # search/listing: a grep-style read, never a specific file's pre-edit state
        _ev(j, eid="r4", kind="read", step=0, stream="s1", path="/*.py", tok=20, tok_attr="attributed",
           representation="search")
        # ambiguous_composite: excluded from the denominator entirely
        _ev(j, eid="r5", kind="read", step=0, stream="s1", path="/c.py", tok=999, tok_attr="ambiguous_composite")
    db = _mk(tmp_path, "j.db", build)
    r = analyze_journal(db)
    assert r["tokens"]["required"] == 100
    assert r["tokens"]["verification"] == 30
    assert r["tokens"]["exploration_reducible"] == 50
    assert r["tokens"]["search_listing_reducible"] == 20
    assert r["excluded_tokens"]["ambiguous_composite"] == 999          # reported, not silently dropped
    assert sum(r["tokens"].values()) == 200                             # 999 is NOT in the denominator


# --------------------------------------------------------------------------- aggregate
def test_aggregate_computes_c_safe_and_c_upper_across_runs(tmp_path):
    def run1(j):
        _ev(j, eid="r1", kind="read", step=0, stream="s1", path="/a.py", tok=60, tok_attr="attributed")   # explore
        _ev(j, eid="r2", kind="read", step=0, stream="s1", path="/*.py", tok=20, tok_attr="attributed",
           representation="search")                                                                        # search
        _ev(j, eid="r3", kind="read", step=0, stream="s1", path="/b.py", tok=20, tok_attr="attributed")
        _ev(j, eid="e3", kind="edit", step=1, stream="s1", path="/b.py", mut="verified_change")            # required

    def run2(j):
        _ev(j, eid="r1", kind="read", step=0, stream="s1", path="/x.py", tok=100, tok_attr="attributed")   # explore

    runs = tmp_path / "runs"; runs.mkdir()
    for name, builder, stratum in (("run-01", run1, "fs1"), ("run-02", run2, "fs2")):
        d = runs / name; d.mkdir()
        db = _mk(d, "journal.sqlite", builder)
        json.dump({"task_id": name, "category": stratum}, open(d / "manifest.json", "w"))

    result = aggregate(str(runs))
    assert result["n_runs"] == 2
    # exploration_reducible = 60 (run1) + 100 (run2) = 160; search_listing_reducible = 20; required = 20
    assert result["bucket_tokens"]["exploration_reducible"] == 160
    assert result["bucket_tokens"]["search_listing_reducible"] == 20
    assert result["bucket_tokens"]["required"] == 20
    assert result["total_fully_measured_tokens"] == 200
    assert result["c_safe"]["ratio"] == 0.8           # 160/200
    assert result["c_upper"]["ratio"] == 0.9          # (160+20)/200
    assert result["c_upper"]["ratio"] >= result["c_safe"]["ratio"]     # upper is never below safe
    assert set(result["by_stratum"]) == {"fs1", "fs2"}
    assert result["by_stratum"]["fs2"]["c_safe"] == 1.0                # run2 is pure exploration


# --------------------------------------------------------------------------- robustness
def test_robustness_micro_macro_and_stratum_standardized_are_independent_reweightings():
    """4 tasks, 2 strata, one task with a MUCH larger token volume than the other 3 -- deliberately
    constructed so micro (token-weighted), macro (task-weighted), and stratum-standardized (equal
    weight per stratum) all land on genuinely DIFFERENT numbers, proving they're independent
    reweightings and not three views of the same arithmetic. Expected values precomputed exactly
    (not approximated) via the same formulas this function implements, verified independently."""
    per_run = [
        {"stratum": "A", "tokens": {"required": 85, "verification": 0, "exploration_reducible": 15,
                                    "search_listing_reducible": 0, "unresolved_other": 0, "config_required": 0}},
        {"stratum": "A", "tokens": {"required": 10, "verification": 0, "exploration_reducible": 90,
                                    "search_listing_reducible": 0, "unresolved_other": 0, "config_required": 0}},
        {"stratum": "A", "tokens": {"required": 50, "verification": 0, "exploration_reducible": 50,
                                    "search_listing_reducible": 0, "unresolved_other": 0, "config_required": 0}},
        {"stratum": "B", "tokens": {"required": 100, "verification": 0, "exploration_reducible": 900,
                                    "search_listing_reducible": 0, "unresolved_other": 0, "config_required": 0}},
    ]
    by_stratum = {
        "A": {"c_safe": round((15 + 90 + 50) / 300, 4), "c_upper": round((15 + 90 + 50) / 300, 4)},
        "B": {"c_safe": round(900 / 1000, 4), "c_upper": round(900 / 1000, 4)},
    }
    rob = compute_robustness(per_run, by_stratum)

    micro_c_safe = round((15 + 90 + 50 + 900) / (100 + 100 + 100 + 1000), 4)
    assert rob["c_safe"]["macro_mean"] == 0.6125
    assert rob["c_safe"]["macro_median"] == 0.7
    assert rob["c_safe"]["n"] == 4
    assert rob["c_safe"]["n_tasks_gt_10pct"] == 4          # 0.15,0.90,0.50,0.90 all > 0.10
    assert rob["c_safe"]["n_tasks_gt_20pct"] == 3          # 0.15 excluded
    assert rob["c_safe"]["stratum_standardized_mean"] == 0.7084   # mean(0.5167, 0.9), rounded
    # the three reweightings must be genuinely distinct here -- that's the point of the fixture
    assert len({micro_c_safe, rob["c_safe"]["macro_mean"], rob["c_safe"]["stratum_standardized_mean"]}) == 3
