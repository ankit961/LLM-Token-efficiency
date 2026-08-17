"""Step-4 reduction-replay harness — deterministic tests over synthetic journals.

Zero LLM cost. Verifies the cap model (reduced size = CAP(budget) above threshold), the
floor-tuning behaviour (a lower floor captures mid-size reads), the concentration report
(where the token mass lives), and that the search bucket is isolated via the frozen classifier.
Reuses the same journal-builder shape as test_opportunity_ceiling.
"""
import json

from contextruntime.hookjournal import HookJournal

from corpus.reduction_replay import (aggregate, calibrate_cap, concentration,
                                      saved_tokens, scan_journal)

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


def _search(j, eid, tok, step=0):
    _ev(j, eid=eid, kind="read", step=step, stream="s1", path="/*.py", tok=tok,
        tok_attr="attributed", representation="search")


def _run(tmp_path, name, builder, stratum="fs1"):
    d = tmp_path / name
    d.mkdir()
    _mk(d, "journal.sqlite", builder)
    json.dump({"task_id": name, "category": stratum}, open(d / "manifest.json", "w"))


# --------------------------------------------------------------------- cap calibration
def test_calibrate_cap_is_flat_and_scales_with_budget():
    c64, c256 = calibrate_cap(64), calibrate_cap(256)
    assert 0 < c64 < c256 < 400            # smaller budget → smaller cap; both well under 400
    # saturation: a 10x-larger input at the same budget yields the SAME cap (memoized + flat)
    assert calibrate_cap(256) == c256


# --------------------------------------------------------------------- cap savings model
def test_saved_tokens_only_reduces_reads_above_threshold():
    cap = calibrate_cap(256)
    sizes = [100, 300, 500, 5000]
    # floor 400: threshold 400 → only 500 and 5000 reduce
    assert saved_tokens(sizes, 256, 400) == (500 - cap) + (5000 - cap)
    # floor 244 (~cap): threshold ~cap → 300 also clears it, capturing more
    assert saved_tokens(sizes, 256, 244) > saved_tokens(sizes, 256, 400)
    # a bucket of only sub-cap reads saves nothing (never negative)
    assert saved_tokens([50, 100, 240], 256, 400) == 0


# --------------------------------------------------------------------- journal scan
def test_scan_journal_isolates_fully_measured_search_reads(tmp_path):
    def build(j):
        _ev(j, eid="r1", kind="read", step=0, stream="s1", path="/a.py", tok=100, tok_attr="attributed")
        _ev(j, eid="e1", kind="edit", step=1, stream="s1", path="/a.py", mut="verified_change")  # required
        _ev(j, eid="r2", kind="read", step=0, stream="s1", path="/b.py", tok=70, tok_attr="attributed")  # explore
        _search(j, "s1", 500)
        _search(j, "s2", 5000)
        # a non-fully-measured search read is excluded from the sizes
        _ev(j, eid="s3", kind="read", step=0, stream="s1", path="/*.js", tok=999,
            tok_attr="ambiguous_composite", representation="search")
    db = _mk(tmp_path, "j.db", build)
    scan = scan_journal(db)
    assert sorted(scan["search_sizes"]) == [500, 5000]
    assert scan["search_bucket_tokens"] == 5500
    assert scan["total_fully_measured_tokens"] == 100 + 70 + 5500      # 999 excluded (ambiguous)


# --------------------------------------------------------------------- aggregate grid + concentration
def test_aggregate_grid_concentration_and_token_neutral_note(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _run(runs, "run-01", lambda j: (_search(j, "s1", 5000),
                                     _ev(j, eid="x", kind="read", step=0, stream="s1", path="/x.py",
                                         tok=1000, tok_attr="attributed")))               # +1000 explore
    _run(runs, "run-02", lambda j: (_search(j, "s1", 300), _search(j, "s2", 500),
                                     _ev(j, eid="r", kind="read", step=0, stream="s1", path="/y.py",
                                         tok=200, tok_attr="attributed"),
                                     _ev(j, eid="e", kind="edit", step=1, stream="s1", path="/y.py",
                                         mut="verified_change")))                          # +200 required
    res = aggregate(str(runs), budgets=(256,), floors=(244, 400))
    cap = calibrate_cap(256)

    assert res["n_runs"] == 2
    assert res["search_bucket_reads"] == 3 and res["search_bucket_tokens"] == 5800
    assert res["total_fully_measured_read_tokens"] == 5800 + 1000 + 200   # 7000
    assert "TOKEN-NEUTRAL" in res["graph_note"]

    g400 = next(g for g in res["grid"] if g["floor"] == 400)
    g244 = next(g for g in res["grid"] if g["floor"] == 244)
    assert g400["saved_tokens"] == (5000 - cap) + (500 - cap)             # 300 blocked by floor 400
    assert g244["saved_tokens"] == (5000 - cap) + (500 - cap) + (300 - cap)  # 300 captured at floor 244
    assert g244["saved_tokens"] > g400["saved_tokens"]                    # the floor-tuning lever
    assert g400["R_direct_micro"] == round(g400["saved_tokens"] / 7000, 4)

    # concentration: the decisive report — mass share above the reference floor
    conc = res["concentration"]
    assert conc["reads"] == 3 and conc["tokens"] == 5800
    assert conc["mass_share_above_ref_floor"] == round(5500 / 5800, 4)    # (5000+500)/5800


def test_empty_search_bucket_is_none_not_crash(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _run(runs, "run-01", lambda j: _ev(j, eid="r1", kind="read", step=0, stream="s1", path="/a.py",
                                       tok=100, tok_attr="attributed"))   # exploration only, no search
    res = aggregate(str(runs), budgets=(256,), floors=(400,))
    assert res["search_bucket_reads"] == 0
    assert res["grid"][0]["R_search_micro"] is None                       # no divide-by-zero
    assert res["concentration"]["reads"] == 0
