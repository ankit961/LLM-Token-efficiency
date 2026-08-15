"""Slice 3A -- observed-label validity reporter, with censoring under adversarial scrutiny.

The reporter must never let an OPEN stream's not-yet-observed future inflate a settled exploration
headline, and must keep W=16 primary while showing {8,32,inf} as sensitivity. Tests build file-backed
HookJournals by direct row insertion so agent-step distances, stream lineages, closure and token
attribution are all controlled exactly.
"""
from contextruntime.classify import EDIT_PRECONDITION, EXPLORATION, UNKNOWN, VERIFICATION
from contextruntime.hookjournal import HookJournal
from contextruntime.labelreport import INF_WINDOW, build_report, stream_closure

_SENTINEL = object()


def _ev(j, *, eid, kind, step, stream, path, cv=None, mut=None, vstat="stable",
        tok=None, tok_attr=None, tstatus=_SENTINEL, channel=None, tool="Read"):
    tstat = ("text" if tok_attr == "attributed" else None) if tstatus is _SENTINEL else tstatus
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": stream,
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": eid, "tool_name": tool, "kind": kind,
        "channel": channel or ("native_read" if kind == "read" else "edit"),
        "mutation_source": None, "mutation_status": mut, "representation": "file",
        "path_absolute": path, "path_normalized": path, "repo_relative": None, "repo_id": None,
        "pre_version": None, "post_version": None, "content_version": cv, "version_status": vstat,
        "response_hash": None, "model_visible_chars": None, "model_visible_tokens": tok,
        "token_status": tstat, "token_attribution": tok_attr,
        "token_estimator_id": "chars4-v1", "success": 1, "outcome": "success",
        "wall_time_ns": None, "schema_version": "0.3.0"})


def _mk(tmp_path, name, fn):
    db = str(tmp_path / name)
    j = HookJournal(db)
    fn(j)
    j.commit()
    j.close()
    return db


def _rows(db):
    import sqlite3
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return [dict(r) for r in c.execute("SELECT * FROM tool_events ORDER BY seq")]


# CENSORING: an OPEN stream's exploration is provisional and drops out of the headline; a CLOSED
# stream's exploration stays. before/after exploration% must differ in the safe (downward) direction.
def test_censoring_excludes_open_stream_exploration_from_headline(tmp_path):
    def build(j):
        # closed stream: one exploration (final) + one precondition (read a then edit a)
        _ev(j, eid="c_expl", kind="read", step=0, stream="closed:main:0", path="/x.py")
        _ev(j, eid="c_read", kind="read", step=1, stream="closed:main:0", path="/y.py")
        _ev(j, eid="c_edit", kind="edit", step=2, stream="closed:main:0", path="/y.py", mut="verified_change")
        # open stream: two explorations (both provisional)
        _ev(j, eid="o_e1", kind="read", step=0, stream="open:main:0", path="/p.py")
        _ev(j, eid="o_e2", kind="read", step=1, stream="open:main:0", path="/q.py")
    db = _mk(tmp_path, "cen.db", build)
    manifest = {"streams": [{"stream_key": "closed:main:0", "closed": True,
                             "closure_reason": "controlled_run_completed"}]}
    rep = build_report(db, manifest=manifest)
    cn = rep["censoring"]
    assert cn["streams_closed"] == 1 and cn["streams_open"] == 1
    assert cn["provisional_exploration_reads_excluded"] == 2          # the two open explorations
    # before: 3 exploration + 1 precondition of 4 -> 75%; after excludes 2 provisional -> 1/2 = 50%
    assert cn["headline_exploration_pct_before"] == 75.0
    assert cn["headline_exploration_pct_after"] == 50.0
    assert cn["headline_exploration_pct_after"] <= cn["headline_exploration_pct_before"]  # safe direction
    assert cn["labels_after"]["counts"][EXPLORATION] == 1                # only the closed one survives
    assert cn["labels_after"]["counts"][EDIT_PRECONDITION] == 1


# REGRESSION for the overstatement bug: dropping provisional VERIFICATION reads must NOT inflate the
# exploration headline. after_pct must stay <= before_pct even when open streams are verification-heavy.
def test_censoring_does_not_inflate_exploration_via_provisional_verification(tmp_path):
    def build(j):
        # open stream: 3 verification rereads (provisional) + 1 bare exploration (provisional)
        for i in range(3):
            p = f"/v{i}.py"
            _ev(j, eid=f"oe{i}", kind="edit", step=0, stream="open:main:0", path=p, mut="verified_change")
            _ev(j, eid=f"or{i}", kind="read", step=1, stream="open:main:0", path=p)   # -> VERIFICATION
        _ev(j, eid="oexpl", kind="read", step=2, stream="open:main:0", path="/q.py")   # -> EXPLORATION
        # closed stream: 1 exploration (final) + 1 precondition (final)
        _ev(j, eid="cexpl", kind="read", step=0, stream="closed:main:0", path="/a.py")
        _ev(j, eid="cr", kind="read", step=1, stream="closed:main:0", path="/b.py")
        _ev(j, eid="ce", kind="edit", step=2, stream="closed:main:0", path="/b.py", mut="verified_change")
    db = _mk(tmp_path, "infl.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "closed:main:0", "closed": True}]})
    cn = rep["censoring"]
    # 6 reads: 3 verification(prov) + 1 exploration(prov) open; 1 exploration(final) + 1 precond closed
    assert cn["provisional_by_class"].get(VERIFICATION) == 3 and cn["provisional_by_class"].get(EXPLORATION) == 1
    assert cn["headline_exploration_pct_before"] == round(100 * 2 / 6, 2)      # 33.33
    # remove ONLY the 1 provisional exploration -> 1/5 = 20%. The naive "drop all provisional" bug
    # would have given 1/2 = 50% -- ABOVE before. Guard against that.
    assert cn["headline_exploration_pct_after"] == 20.0
    assert cn["headline_exploration_pct_after"] <= cn["headline_exploration_pct_before"]


# A /clear that starts a newer lineage epoch auto-closes the PRIOR epoch -- no manifest needed.
def test_clear_epoch_auto_closes_prior_lineage(tmp_path):
    def build(j):
        _ev(j, eid="e0", kind="read", step=0, stream="s:main:0", path="/a.py")   # prior epoch
        _ev(j, eid="e1", kind="read", step=0, stream="s:main:1", path="/b.py")   # newer epoch exists
    db = _mk(tmp_path, "clr.db", build)
    closure = stream_closure(_rows(db))                              # raw keys via the helper (report pseudonymizes)
    assert closure["s:main:0"]["closed"] is True
    assert closure["s:main:0"]["closure_reason"] == "superseded_clear_epoch"
    assert closure["s:main:1"]["closed"] is False                    # newest epoch stays open
    rep = build_report(db)                                            # NO manifest
    # the prior-epoch exploration is FINAL; only the open (newer) epoch's exploration is provisional
    assert rep["censoring"]["provisional_exploration_reads_excluded"] == 1


# WINDOW SENSITIVITY: a future edit at distance 12 is out-of-window at 8 (UNKNOWN) but a precondition
# at 16/32/inf -> stability_precondition < 1 and the transition matrix records the flip.
def test_window_sensitivity_and_precondition_stability(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/z.py")
        _ev(j, eid="e", kind="edit", step=12, stream="s:main:0", path="/z.py", mut="verified_change")
    db = _mk(tmp_path, "win.db", build)
    manifest = {"streams": [{"stream_key": "s:main:0", "closed": True}]}
    rep = build_report(db, manifest=manifest)
    ws = rep["window_sensitivity"]
    assert ws["class_counts_by_window"]["8"][UNKNOWN] == 1 and ws["class_counts_by_window"]["8"][EDIT_PRECONDITION] == 0
    assert ws["class_counts_by_window"]["16"][EDIT_PRECONDITION] == 1
    assert ws["class_counts_by_window"]["inf"][EDIT_PRECONDITION] == 1
    assert ws["stability_precondition"] == 0.0                        # P8 is empty -> 0/1
    assert ws["invariant_fraction"] == 0.0                            # r's label changes across W
    assert ws["transition_matrices_vs_primary"]["8"][EDIT_PRECONDITION][UNKNOWN] == 1


# CAUSAL DISTANCE percentiles + histogram for preconditions (agent steps).
def test_causal_distance_percentiles(tmp_path):
    def build(j):
        for i, d in enumerate((1, 2, 4, 8)):
            p = f"/f{i}.py"
            _ev(j, eid=f"r{i}", kind="read", step=0, stream="s:main:0", path=p)
            _ev(j, eid=f"e{i}", kind="edit", step=d, stream="s:main:0", path=p, mut="verified_change")
    db = _mk(tmp_path, "dist.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    cd = rep["causal_distance_precondition"]
    assert cd["n"] == 4 and cd["max"] == 8
    assert cd["p50"] == 3.0 and cd["p90"] == 6.8                      # linear interp of [1,2,4,8]
    assert cd["histogram"] == {"1": 1, "2": 1, "3-4": 1, "5-8": 1}
    assert cd["seq_fallback_count"] == 0


# TOKENS: an ambiguous-multipath read (NULL token weight) is EXCLUDED from token-share, never counted
# as zero cost; coverage reflects it and the attributed sum ignores it.
def test_token_attribution_coverage_excludes_ambiguous(tmp_path):
    def build(j):
        _ev(j, eid="a", kind="read", step=0, stream="s:main:0", path="/a.py",
            tok=100, tok_attr="attributed")
        _ev(j, eid="b", kind="read", step=1, stream="s:main:0", path="/b.py",
            tok=None, tok_attr="ambiguous_multipath")
    db = _mk(tmp_path, "tok.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    tk = rep["tokens"]
    assert tk["attribution_coverage"] == 50.0 and tk["fully_measured_reads"] == 1
    assert tk["fully_measured_tokens_total"] == 100                  # the ambiguous NULL is not zero-summed
    assert tk["event_share"][EXPLORATION] == 100.0                   # both reads are exploration by events
    assert tk["token_share_fully_measured"][EXPLORATION] == 100.0    # but token-share is over fully-measured only
    assert tk["measurement_breakdown"]["ambiguous_multipath"] == 1
    assert rep["capture_integrity"]["reads_missing_or_ambiguous_token_weight"] == 1


# PROVENANCE + INTEGRITY: the artifact must be self-describing and lead with capture trust.
def test_provenance_and_integrity_fields(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py", vstat="stable")
        _ev(j, eid="e", kind="edit", step=1, stream="s:main:0", path="/a.py", mut="verified_change")
    db = _mk(tmp_path, "prov.db", build)
    rep = build_report(db, runtime_commit_sha="deadbeef", client_version="2.1.229")
    p = rep["provenance"]
    assert p["report_schema_version"] == "label-report-0.2.0" and p["hook_schema_version"] == "0.4.0"
    assert p["runtime_commit_sha"] == "deadbeef" and p["client_version"] == "2.1.229"
    assert p["classifier_blob_sha"].startswith("sha256:")        # identity of classify.py source
    assert p["journal_sha256"].startswith("sha256:") and p["primary_window"] == 16
    assert p["token_estimator_id"] == "chars4-v1" and p["windows"] == ["8", "16", "32", "inf"]
    ig = rep["capture_integrity"]
    assert ig["version_status_counts"] == {"stable": 1} and ig["mutation_status_counts"] == {"verified_change": 1}
    assert ig["reads_classified"] == 1 and ig["edits"] == 1


# W=16 is never tuned: a mixed journal reports the SAME primary window regardless of exploration share.
def test_primary_window_is_fixed_not_tuned(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py")
    db = _mk(tmp_path, "fix.db", build)
    rep = build_report(db)
    assert rep["labels_primary"]["window"] == 16 and rep["provenance"]["primary_window"] == 16


def test_stream_closure_helper_direct():
    rows = [{"stream_key": "s:main:0", "seq": 1}, {"stream_key": "s:main:1", "seq": 2},
            {"stream_key": "t:main:0", "seq": 3}]
    cl = stream_closure(rows, {"streams": [{"stream_key": "t:main:0", "closed": True,
                                            "closure_reason": "controlled_run_completed", "closed_at_seq": 3}]})
    assert cl["s:main:0"]["closed"] is True and cl["s:main:0"]["closure_reason"] == "superseded_clear_epoch"
    assert cl["s:main:1"]["closed"] is False                         # newest epoch
    assert cl["t:main:0"]["closed"] is True and cl["t:main:0"]["closed_at_seq"] == 3


# AUDIT FIX (closed_at_seq enforcement): reads BEYOND a stream's declared closed_at_seq are NOT
# settled -- they happened after observation ended, so they stay provisional (right-censored).
def test_reads_after_closed_at_seq_stay_provisional(tmp_path):
    def build(j):
        for i in range(4):                                       # 4 exploration reads at seq 1..4
            _ev(j, eid=f"r{i}", kind="read", step=i, stream="run:main:0", path=f"/f{i}.py")
    db = _mk(tmp_path, "cseq.db", build)
    manifest = {"streams": [{"stream_key": "run:main:0", "closed": True, "closed_at_seq": 1}]}
    rep = build_report(db, manifest=manifest)
    cn = rep["censoring"]
    # only the seq<=1 read is settled; the 3 reads at seq 2,3,4 are post-closure -> provisional
    assert cn["provisional_exploration_reads_excluded"] == 3
    assert cn["headline_exploration"]["final_exploration_reads"] == 1


# AUDIT FIX (prior_unverified_mutation is absence-derived): a read after an UNVERIFIED prior edit with
# no future edit is UNKNOWN(prior_unverified_mutation) and IS flippable by a future edit -> provisional.
def test_prior_unverified_mutation_read_is_provisional_when_open(tmp_path):
    def build(j):
        _ev(j, eid="e", kind="edit", step=0, stream="s:main:0", path="/a.py", mut="unverified")
        _ev(j, eid="r", kind="read", step=1, stream="s:main:0", path="/a.py")   # UNKNOWN(prior_unverified)
    db = _mk(tmp_path, "puv.db", build)
    rep = build_report(db)                                       # open stream, no manifest
    assert rep["labels_primary"]["by_reason"].get("prior_unverified_mutation") == 1
    assert rep["censoring"]["provisional_reads_total"] == 1      # counted as censored, not settled
    assert rep["censoring"]["provisional_by_class"].get(UNKNOWN) == 1


# AUDIT FIX (token honesty, HIGH): a single-path MULTIMODAL read is attributed with NULL tokens; it
# must NOT count as covered, must NOT be zero-summed, and coverage must never exceed 100%.
def test_multimodal_null_token_read_is_not_counted_as_attributed(tmp_path):
    def build(j):
        _ev(j, eid="txt", kind="read", step=0, stream="s:main:0", path="/a.py", tok=100, tok_attr="attributed")
        # image read: upstream stamps attributed but NULL weight, token_status='multimodal'
        _ev(j, eid="img", kind="read", step=1, stream="s:main:0", path="/b.png",
            tok=None, tok_attr="attributed", tstatus="multimodal")
    db = _mk(tmp_path, "mm.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    tk = rep["tokens"]
    assert tk["fully_measured_reads"] == 1                                   # img NOT fully measured
    assert tk["attribution_coverage"] == 50.0                                # never >100
    assert tk["fully_measured_tokens_total"] == 100                          # img's NULL not summed as 0
    assert tk["measurement_breakdown"]["multimodal_unmeasured"] == 1
    assert rep["capture_integrity"]["reads_missing_or_ambiguous_token_weight"] == 1   # self-check sees img


# AUDIT FIX (coverage cannot exceed 100%): a failed-but-attributed read (dropped by to_events) must
# not enter the token denominator, which counts only classified reads.
def test_failed_attributed_read_does_not_break_coverage(tmp_path):
    def build(j):
        _ev(j, eid="ok", kind="read", step=0, stream="s:main:0", path="/a.py", tok=40, tok_attr="attributed")
        # a failed read: success=0 -> dropped by to_events, so NOT in the classified set
        j.put_tool_event({
            "event_id": "bad", "session_id": "s", "agent_id": None, "stream_key": "s:main:0",
            "prompt_id": None, "cwd": None, "step": 1, "batch_id": None, "batch_size": None,
            "parallel": None, "tool_use_id": "bad", "tool_name": "Read", "kind": "read",
            "channel": "native_read", "mutation_source": None, "mutation_status": None,
            "representation": "file", "path_absolute": "/x.py", "path_normalized": "/x.py",
            "repo_relative": None, "repo_id": None, "pre_version": None, "post_version": None,
            "content_version": None, "version_status": "unverified", "response_hash": None,
            "model_visible_chars": None, "model_visible_tokens": 999, "token_status": "text",
            "token_attribution": "attributed", "token_estimator_id": "chars4-v1",
            "success": 0, "outcome": "failed", "wall_time_ns": None, "schema_version": "0.3.0"})
    db = _mk(tmp_path, "fail.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    tk = rep["tokens"]
    assert tk["attribution_coverage"] == 100.0 and tk["fully_measured_reads"] == 1  # only the 1 classified read
    assert tk["fully_measured_tokens_total"] == 40                                  # failed read's 999 excluded


# AUDIT FIX (closure lineage robustness): a plugin-scoped agent id containing ':' must not confuse
# lineage grouping -- two epochs of the SAME agent auto-close correctly; a different agent does not.
def test_closure_lineage_uses_identity_columns_not_key_split(tmp_path):
    def build(j):
        # same session S, same agent 'plug:rev' (colon in agent id), epochs 0 then 1
        for ep, eid in ((0, "a"), (1, "b")):
            j.put_tool_event({
                "event_id": eid, "session_id": "S", "agent_id": "plug:rev",
                "stream_key": f"S:plug:rev:{ep}", "prompt_id": None, "cwd": None, "step": 0,
                "batch_id": None, "batch_size": None, "parallel": None, "tool_use_id": eid,
                "tool_name": "Read", "kind": "read", "channel": "native_read", "mutation_source": None,
                "mutation_status": None, "representation": "file", "path_absolute": f"/{eid}.py",
                "path_normalized": f"/{eid}.py", "repo_relative": None, "repo_id": None,
                "pre_version": None, "post_version": None, "content_version": None,
                "version_status": "stable", "response_hash": None, "model_visible_chars": None,
                "model_visible_tokens": 10, "token_status": "text", "token_attribution": "attributed",
                "token_estimator_id": "chars4-v1", "success": 1, "outcome": "success",
                "wall_time_ns": None, "schema_version": "0.3.0"})
    db = _mk(tmp_path, "lin.db", build)
    closure = stream_closure(_rows(db))                         # raw keys via the helper
    assert closure["S:plug:rev:0"]["closed"] is True            # prior epoch auto-closed
    assert closure["S:plug:rev:0"]["closure_reason"] == "superseded_clear_epoch"
    assert closure["S:plug:rev:1"]["closed"] is False           # newest epoch stays open


def test_inf_window_present_and_unbounded(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/z.py")
        _ev(j, eid="e", kind="edit", step=500, stream="s:main:0", path="/z.py", mut="verified_change")
    db = _mk(tmp_path, "inf.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]},
                       canonical=False, windows=(16, INF_WINDOW), primary=16)
    # distance 500 is outside 16 (UNKNOWN) but inside inf (precondition)
    assert rep["window_sensitivity"]["class_counts_by_window"]["16"][UNKNOWN] == 1
    assert rep["window_sensitivity"]["class_counts_by_window"]["inf"][EDIT_PRECONDITION] == 1
    assert rep["provenance"]["canonical_report"] is False        # experimental windows stamp non-canonical


# 3A.1 (canonical locking): the default run is canonical + fixed; experimental windows are stamped;
# a primary outside the window set is rejected rather than KeyError-ing.
def test_canonical_window_locking(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py")
    db = _mk(tmp_path, "canon.db", build)
    rep = build_report(db)
    assert rep["provenance"]["canonical_report"] is True
    assert rep["provenance"]["windows"] == ["8", "16", "32", "inf"] and rep["provenance"]["primary_window"] == 16
    # the stability note reflects the ACTUAL windows, not a hardcoded set
    assert rep["window_sensitivity"]["stability_precondition_note"] == "|P8 ∩ P16 ∩ P32 ∩ Pinf| / |P16|"
    # experimental custom set is honored but stamped non-canonical, with its own note
    exp = build_report(db, canonical=False, windows=(16, 64), primary=16)
    assert exp["provenance"]["canonical_report"] is False
    assert exp["window_sensitivity"]["stability_precondition_note"] == "|P16 ∩ P64| / |P16|"
    # a primary not among the windows is a hard error, not a silent KeyError
    import pytest
    with pytest.raises(ValueError):
        build_report(db, canonical=False, windows=(8, 32), primary=16)


# 3A.1 (token censoring): the exploration TOKEN share is right-censored too -- an open-stream
# provisional exploration read's tokens leave num+denom for after, giving floor <= after <= before.
def test_exploration_token_censoring_interval(tmp_path):
    def build(j):
        # closed stream: 1 exploration read worth 40 tokens (final)
        _ev(j, eid="cf", kind="read", step=0, stream="closed:main:0", path="/a.py",
            tok=40, tok_attr="attributed")
        # open stream: 1 exploration read worth 300 tokens (provisional) + 1 precondition-ish anchor
        _ev(j, eid="op", kind="read", step=0, stream="open:main:0", path="/b.py",
            tok=300, tok_attr="attributed")
        _ev(j, eid="anchor_r", kind="read", step=1, stream="open:main:0", path="/c.py",
            tok=20, tok_attr="attributed")
        _ev(j, eid="anchor_e", kind="edit", step=2, stream="open:main:0", path="/c.py", mut="verified_change")
    db = _mk(tmp_path, "tcen.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "closed:main:0", "closed": True}]})
    et = rep["censoring"]["exploration_tokens"]
    # fully-measured exploration tokens: final=40 (closed), provisional=300 (open). total fully=360.
    # before=(40+300)/360=94.44; after=40/(360-300)=66.67; floor=40/360=11.11.
    assert et["floor_pct"] <= et["after_pct"] <= et["before_pct"]
    assert et["before_pct"] == round(100 * 340 / 360, 2)
    assert et["after_pct"] == round(100 * 40 / 60, 2)
    assert et["floor_pct"] == round(100 * 40 / 360, 2)
    assert et["exploration_tokens_provisional"] == 300 and et["exploration_tokens_final"] == 40


# 3A.1 (partial multimodal honesty): a text+image read carries only text tokens; it stays OUT of the
# fully-measured denominator but its partial estimate is reported, never silently full-counted.
def test_partial_multimodal_kept_out_of_denominator(tmp_path):
    def build(j):
        _ev(j, eid="full", kind="read", step=0, stream="s:main:0", path="/a.py", tok=80, tok_attr="attributed")
        _ev(j, eid="part", kind="read", step=1, stream="s:main:0", path="/b.py",
            tok=50, tok_attr="attributed", tstatus="text_partial_multimodal")
    db = _mk(tmp_path, "pmm.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    tk = rep["tokens"]
    assert tk["measurement_breakdown"]["fully_attributed_text"] == 1
    assert tk["measurement_breakdown"]["partial_multimodal"] == 1
    assert tk["fully_measured_tokens_total"] == 80                # partial 50 NOT in the denominator
    assert tk["partial_multimodal_text_tokens"] == 50            # but reported separately
    assert tk["fully_measured_reads"] == 1


# 3A.1 (privacy): the shared artifact carries NO raw stream_key -- only stream_NNN pseudonyms.
def test_shared_artifact_pseudonymizes_stream_ids(tmp_path):
    import json as _json
    import re
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="1ac3b280-0a20-448d-9955-27c9806f7dc0:main:0", path="/a.py")
    db = _mk(tmp_path, "priv.db", build)
    rep = build_report(db)
    keys = list(rep["censoring"]["closure_by_stream"])
    assert keys and all(re.fullmatch(r"stream_\d{3,}", k) for k in keys)
    assert all(re.fullmatch(r"stream_\d{3,}", k) for k in rep["provenance"]["closure_manifest"])
    assert "1ac3b280" not in _json.dumps(rep)                    # the raw session id never appears
    assert "_stream_key_map" not in rep                          # not emitted by default
    # the raw map is available only on explicit opt-in (for a LOCAL file)
    rep2 = build_report(db, include_stream_map=True)
    assert any("1ac3b280" in raw for raw in rep2["_stream_key_map"].values())


# 3A.1 (validity gate): a precondition that relied on SEQ fallback (no agent step) fails canonical
# validity rather than silently entering the W=16 evidence set.
def test_seq_fallback_precondition_fails_canonical_validity(tmp_path):
    def build(j):
        # no step on either event -> classifier falls back to seq distance for the precondition
        _ev(j, eid="r", kind="read", step=None, stream="s:main:0", path="/a.py")
        _ev(j, eid="e", kind="edit", step=None, stream="s:main:0", path="/a.py", mut="verified_change")
    db = _mk(tmp_path, "seqf.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    va = rep["validity"]
    assert va["seq_fallback_preconditions"] >= 1
    assert va["canonical_validity"] is False                     # step integrity gate tripped
    assert va["missing_step_reads"] == 1 and va["missing_step_mutations"] == 1


def test_all_canonical_when_steps_present(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py")
        _ev(j, eid="e", kind="edit", step=1, stream="s:main:0", path="/a.py", mut="verified_change")
    db = _mk(tmp_path, "okval.db", build)
    va = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})["validity"]
    assert va["canonical_validity"] is True and va["seq_fallback_preconditions"] == 0


# 3A.2 (stricter validity): a MISSING step that never produced a precondition (a read with no future
# edit) must still fail canonical_validity -- a missing step can flip an outside-window decision.
def test_missing_step_without_precondition_fails_validity(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=None, stream="s:main:0", path="/a.py")   # no edit -> exploration
    db = _mk(tmp_path, "misstep.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    va = rep["validity"]
    assert va["missing_step_reads"] == 1 and va["seq_fallback_preconditions"] == 0
    assert va["canonical_validity"] is False                     # missing step alone trips the gate
    assert rep["admission"]["checks"]["canonical_validity"] is False


# 3A.2 (exact admission gate): a clean run with complete provenance is canonical_admissible; the
# checks are the exact mechanical booleans the corpus harness applies.
def test_admission_gate_exact_checks(tmp_path):
    def build(j):
        j.bump("deliveries"); j.bump("pre_tool_calls_seen"); j.bump("batch_tool_calls_resolved")
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py", tok=10, tok_attr="attributed")
        _ev(j, eid="e", kind="edit", step=1, stream="s:main:0", path="/a.py", mut="verified_change")
    db = _mk(tmp_path, "adm.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]},
                       runtime_commit_sha="abc123", client_version="2.1.229")
    ad = rep["admission"]
    assert ad["checks"] == {"pre_equals_batch": True, "zero_errors": True, "zero_pending": True,
                            "hook_schema_expected": True, "canonical_report": True,
                            "canonical_validity": True, "provenance_complete": True}
    assert ad["canonical_admissible"] is True
    assert ad["token_share_eligible"] is True and ad["bash_coverage_clean"] is True


# 3A.2 (provenance completeness): a canonical run WITHOUT client/runtime stamps is NOT admissible.
def test_admission_requires_complete_provenance(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py")
    db = _mk(tmp_path, "prov2.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})  # no stamps
    assert rep["admission"]["checks"]["provenance_complete"] is False
    assert rep["admission"]["canonical_admissible"] is False


# 3A.2 (token-share eligibility is SEPARATE): a run with a multimodal read is still event-admissible
# but NOT token-share eligible.
def test_token_share_eligibility_is_separate_from_admission(tmp_path):
    def build(j):
        j.bump("deliveries"); j.bump("pre_tool_calls_seen"); j.bump("batch_tool_calls_resolved")
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py", tok=10, tok_attr="attributed")
        _ev(j, eid="img", kind="read", step=1, stream="s:main:0", path="/b.png",
            tok=None, tok_attr="attributed", tstatus="multimodal")
    db = _mk(tmp_path, "tse.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]},
                       runtime_commit_sha="abc", client_version="2.1.229")
    assert rep["admission"]["canonical_admissible"] is True       # event-label analysis still fine
    assert rep["admission"]["token_share_eligible"] is False      # but not the token headline


# 3A.2 (closure_reason is an enum): free-text closure_reason is rejected (could leak identifiers).
def test_free_text_closure_reason_is_rejected(tmp_path):
    import pytest
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py")
    db = _mk(tmp_path, "cr.db", build)
    manifest = {"streams": [{"stream_key": "s:main:0", "closed": True,
                             "closure_reason": "user Ankit's laptop run"}]}
    with pytest.raises(ValueError):
        build_report(db, manifest=manifest)
    # the four enum values are accepted
    for reason in ("controlled_run_completed", "timeout", "aborted"):
        ok = {"streams": [{"stream_key": "s:main:0", "closed": True, "closure_reason": reason}]}
        assert build_report(db, manifest=ok)["censoring"]["streams_closed"] == 1
