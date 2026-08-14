"""Slice 3A -- observed-label validity reporter, with censoring under adversarial scrutiny.

The reporter must never let an OPEN stream's not-yet-observed future inflate a settled exploration
headline, and must keep W=16 primary while showing {8,32,inf} as sensitivity. Tests build file-backed
HookJournals by direct row insertion so agent-step distances, stream lineages, closure and token
attribution are all controlled exactly.
"""
from contextruntime.classify import EDIT_PRECONDITION, EXPLORATION, UNKNOWN, VERIFICATION
from contextruntime.hookjournal import HookJournal
from contextruntime.labelreport import INF_WINDOW, build_report, stream_closure


def _ev(j, *, eid, kind, step, stream, path, cv=None, mut=None, vstat="stable",
        tok=None, tok_attr=None, channel=None, tool="Read"):
    j.put_tool_event({
        "event_id": eid, "session_id": "s", "agent_id": None, "stream_key": stream,
        "prompt_id": None, "cwd": None, "step": step, "batch_id": None, "batch_size": None,
        "parallel": None, "tool_use_id": eid, "tool_name": tool, "kind": kind,
        "channel": channel or ("native_read" if kind == "read" else "edit"),
        "mutation_source": None, "mutation_status": mut, "representation": "file",
        "path_absolute": path, "path_normalized": path, "repo_relative": None, "repo_id": None,
        "pre_version": None, "post_version": None, "content_version": cv, "version_status": vstat,
        "response_hash": None, "model_visible_chars": None, "model_visible_tokens": tok,
        "token_status": "text" if tok_attr == "attributed" else None, "token_attribution": tok_attr,
        "token_estimator_id": "chars4-v1", "success": 1, "outcome": "success",
        "wall_time_ns": None, "schema_version": "0.3.0"})


def _mk(tmp_path, name, fn):
    db = str(tmp_path / name)
    j = HookJournal(db)
    fn(j)
    j.commit()
    j.close()
    return db


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
    rep = build_report(db)                                            # NO manifest
    closure = rep["censoring"]["closure_by_stream"]
    assert closure["s:main:0"]["closed"] is True
    assert closure["s:main:0"]["closure_reason"] == "superseded_clear_epoch"
    assert closure["s:main:1"]["closed"] is False                    # newest epoch stays open
    # the prior-epoch exploration is FINAL, so not provisional
    assert rep["labels_primary"]["provisional_reads"] >= 0
    assert rep["censoring"]["provisional_exploration_reads_excluded"] == 1   # only the open (newer) one


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
    assert tk["attribution_coverage"] == 50.0 and tk["reads_unattributed"] == 1
    assert tk["attributed_tokens_total"] == 100                      # the ambiguous NULL is not zero-summed
    assert tk["event_share"][EXPLORATION] == 100.0                   # both reads are exploration by events
    assert tk["token_share_attributed"][EXPLORATION] == 100.0        # but token-share is over attributed only
    assert rep["capture_integrity"]["reads_missing_or_ambiguous_token_weight"] == 1


# PROVENANCE + INTEGRITY: the artifact must be self-describing and lead with capture trust.
def test_provenance_and_integrity_fields(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/a.py", vstat="stable")
        _ev(j, eid="e", kind="edit", step=1, stream="s:main:0", path="/a.py", mut="verified_change")
    db = _mk(tmp_path, "prov.db", build)
    rep = build_report(db, classifier_sha="deadbeef", client_version="2.1.229")
    p = rep["provenance"]
    assert p["report_schema_version"] == "label-report-0.1.0" and p["hook_schema_version"] == "0.3.0"
    assert p["classifier_sha"] == "deadbeef" and p["client_version"] == "2.1.229"
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
        # image read: upstream stamps attributed but NULL weight (measure returns multimodal/None)
        _ev(j, eid="img", kind="read", step=1, stream="s:main:0", path="/b.png", tok=None, tok_attr="attributed")
    db = _mk(tmp_path, "mm.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]})
    tk = rep["tokens"]
    assert tk["reads_attributed"] == 1 and tk["reads_unattributed"] == 1     # img NOT counted attributed
    assert tk["attribution_coverage"] == 50.0                                # never >100
    assert tk["attributed_tokens_total"] == 100                              # img's NULL not summed as 0
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
    assert tk["attribution_coverage"] == 100.0 and tk["reads_unattributed"] == 0   # only the 1 classified read
    assert tk["attributed_tokens_total"] == 40                                     # failed read's 999 excluded


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
    rep = build_report(db)
    closure = rep["censoring"]["closure_by_stream"]
    assert closure["S:plug:rev:0"]["closed"] is True             # prior epoch auto-closed
    assert closure["S:plug:rev:0"]["closure_reason"] == "superseded_clear_epoch"
    assert closure["S:plug:rev:1"]["closed"] is False            # newest epoch stays open


def test_inf_window_present_and_unbounded(tmp_path):
    def build(j):
        _ev(j, eid="r", kind="read", step=0, stream="s:main:0", path="/z.py")
        _ev(j, eid="e", kind="edit", step=500, stream="s:main:0", path="/z.py", mut="verified_change")
    db = _mk(tmp_path, "inf.db", build)
    rep = build_report(db, manifest={"streams": [{"stream_key": "s:main:0", "closed": True}]}, windows=(16, INF_WINDOW))
    # distance 500 is outside 16 (UNKNOWN) but inside inf (precondition)
    assert rep["window_sensitivity"]["class_counts_by_window"]["16"][UNKNOWN] == 1
    assert rep["window_sensitivity"]["class_counts_by_window"]["inf"][EDIT_PRECONDITION] == 1
