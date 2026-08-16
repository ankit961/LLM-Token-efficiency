"""corpus/aggregate_grading.py -- merging official SWE-bench report.json across shards.

The one subtlety this locks in: an EMPTY submitted patch is a genuine task outcome (no fix was
produced -> unresolved) that the harness deliberately does not grade (no report.json). That must
be counted as `resolved=False`, NOT `grader_error=True` -- conflating the two would silently
inflate the denominator of "grading infrastructure failed" and could hide a real capability gap
(a task the agent gave up on) behind an error label. Only a TRULY missing report for a non-empty
patch (a real harness/infra failure -- crash, timeout, image pull failure) is a grader_error.
"""
import json
import os

from corpus.aggregate_grading import aggregate


def _index(tmp_path, entries):
    p = tmp_path / "run_index.json"
    p.write_text(json.dumps(entries))
    return str(p)


def _report(tmp_path, shard, instance_id, resolved, f2p_ok=True, p2p_ok=True):
    d = tmp_path / f"shard-{shard}" / "logs" / "run_evaluation" / "r" / "m" / instance_id
    d.mkdir(parents=True)
    (d / "report.json").write_text(json.dumps({
        instance_id: {
            "patch_is_None": False, "patch_exists": True, "patch_successfully_applied": True,
            "resolved": resolved,
            "tests_status": {
                "FAIL_TO_PASS": {"success": ["t1"] if f2p_ok else [], "failure": [] if f2p_ok else ["t1"]},
                "PASS_TO_PASS": {"success": ["t2"] if p2p_ok else [], "failure": [] if p2p_ok else ["t2"]},
            },
        }
    }))


def _run_summary(tmp_path, shard, empty_patch_ids):
    (tmp_path / f"shard-{shard}").mkdir(exist_ok=True)
    (tmp_path / f"shard-{shard}" / "model.run-shardX.json").write_text(json.dumps({
        "total_instances": 1, "empty_patch_ids": empty_patch_ids, "error_instances": 0,
    }))


def test_empty_patch_counts_as_unresolved_not_grader_error(tmp_path):
    idx = _index(tmp_path, {"demo__demo-1": {"stratum": "fs1", "empty_patch": True}})
    _run_summary(tmp_path, 0, ["demo__demo-1"])          # no report.json for it -- harness skipped grading
    summary = aggregate(str(tmp_path), idx)
    row = summary["tasks"][0]
    assert row["resolved"] is False
    assert row["grader_error"] is False
    assert row["empty_patch"] is True
    assert summary["n_grader_errors"] == 0


def test_missing_report_for_a_nonempty_patch_is_a_real_grader_error(tmp_path):
    idx = _index(tmp_path, {"demo__demo-2": {"stratum": "fs1", "empty_patch": False}})
    _run_summary(tmp_path, 0, [])                        # not in empty_patch_ids -> genuinely missing
    summary = aggregate(str(tmp_path), idx)
    row = summary["tasks"][0]
    assert row["resolved"] is None
    assert row["grader_error"] is True
    assert summary["n_grader_errors"] == 1


def test_graded_task_reports_f2p_p2p_and_resolved_from_report_json(tmp_path):
    idx = _index(tmp_path, {"demo__demo-3": {"stratum": "fs2", "empty_patch": False}})
    _report(tmp_path, 0, "demo__demo-3", resolved=True, f2p_ok=True, p2p_ok=True)
    summary = aggregate(str(tmp_path), idx)
    row = summary["tasks"][0]
    assert row["resolved"] is True and row["fail_to_pass_passed"] and row["pass_to_pass_passed"]
    assert summary["n_resolved"] == 1 and summary["success_rate"] == 1.0


def test_per_stratum_success_rate_and_error_counts(tmp_path):
    idx = _index(tmp_path, {
        "d-1": {"stratum": "fs1", "empty_patch": False},
        "d-2": {"stratum": "fs1", "empty_patch": True},
        "d-3": {"stratum": "fs2", "empty_patch": False},
    })
    _report(tmp_path, 0, "d-1", resolved=True)
    _report(tmp_path, 0, "d-3", resolved=False, f2p_ok=False)
    _run_summary(tmp_path, 0, ["d-2"])
    summary = aggregate(str(tmp_path), idx)
    assert summary["n_tasks"] == 3 and summary["n_resolved"] == 1 and summary["n_grader_errors"] == 0
    assert summary["by_stratum"]["fs1"] == {"n": 2, "resolved": 1, "grader_errors": 0, "success_rate": 0.5}
    assert summary["by_stratum"]["fs2"]["success_rate"] == 0.0
