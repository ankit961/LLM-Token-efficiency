"""Corpus runner orchestration -- exercised end-to-end with mock agent + stub evaluator (zero cost).

Proves the chain: clean worktree -> agent -> journal -> closure -> label-report -> admission -> patch
-> evaluation -> manifest -> reproducible artifact hashes, before any real Claude run.
"""
import json
import os
import subprocess

from contextruntime.corpusrunner import (CorpusRunner, LocalStubEvaluator, MockAgentBackend,
                                         parse_spec, verify_spec)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path):
    """A tiny stand-in for the django mirror: one commit whose SHA is the task base_commit."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "a.py").write_text("def f(): pass\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    return str(repo), sha


def _spec(tmp_path, base_commit):
    p = tmp_path / "run-01.md"
    p.write_text(
        "# Task spec — run-01  (IMMUTABLE)\n\n"
        "task_id: django__django-00001\n"
        "category: fs1_oneline_1f_le3\n"
        "repo_id: django/django\n"
        f"base_commit_sha: {base_commit}\n\n"
        "## Prompt (verbatim issue text the agent sees)\nFix the thing.\n")
    return str(p)


def _run(tmp_path):
    repo, sha = _repo(tmp_path)
    spec = parse_spec(_spec(tmp_path, sha))
    runner = CorpusRunner(repo, str(tmp_path / "runs"), MockAgentBackend(), LocalStubEvaluator(),
                          runtime_sha="59d1904", runtime_tag="obs-runtime-3a-v2.1")
    manifest = runner.run_one(spec, expected_spec_sha=spec.spec_sha256,
                              start_time="T0", end_time="T1")
    return tmp_path, spec, manifest


def test_run_produces_the_full_immutable_artifact_set(tmp_path):
    _, spec, manifest = _run(tmp_path)
    run_dir = tmp_path / "runs" / "run-01"
    for f in ("manifest.json", "journal.sqlite", "label-report.json", "label-report.txt",
              "agent.patch", "agent-result.json", "evaluation.json", "hashes.json"):
        assert (run_dir / f).exists(), f
    # worktree was cleaned up
    assert not (run_dir / "worktree").exists()


def test_manifest_stamps_runtime_and_health(tmp_path):
    _, spec, m = _run(tmp_path)
    assert m["task_id"] == "django__django-00001" and m["task_spec_sha256"] == spec.spec_sha256
    assert m["runtime_tag"] == "obs-runtime-3a-v2.1" and m["hook_schema"] == "0.4.1"
    assert m["report_schema"] == "label-report-0.2.1" and m["agent"] == "mock"
    assert m["termination_reason"] == "completed" and m["budget_turns"] == 2
    # the pilot health gate
    assert m["capture_errors"] == 0 and m["pending_tools"] == 0 and m["pre_capture_rate"] == 1.0
    assert m["evaluation_status"] == "eval_deferred"        # agent stage is separate from grading


def test_artifact_hashes_match_files(tmp_path):
    tp, _, m = _run(tmp_path)
    run_dir = tp / "runs" / "run-01"
    hashes = json.loads((run_dir / "hashes.json").read_text())
    import hashlib
    for name, h in hashes.items():
        actual = "sha256:" + hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
        assert actual == h, name
    assert m["journal_sha256"] == hashes["journal.sqlite"]


def test_run_is_reproducible(tmp_path):
    # re-running the SAME task in the SAME location (deterministic mock) -> identical artifact hashes
    repo, sha = _repo(tmp_path)
    spec = parse_spec(_spec(tmp_path, sha))
    runner = CorpusRunner(repo, str(tmp_path / "runs"), MockAgentBackend(), LocalStubEvaluator(),
                          runtime_sha="59d1904")
    m1 = runner.run_one(spec, expected_spec_sha=spec.spec_sha256)
    m2 = runner.run_one(spec, expected_spec_sha=spec.spec_sha256)
    assert m1["journal_sha256"] == m2["journal_sha256"]        # location-stable + deterministic
    assert m1["patch_sha256"] == m2["patch_sha256"]
    # the patch hash is location-INDEPENDENT (no paths) -- stable everywhere
    _, _, m3 = _run(tmp_path / "elsewhere")
    assert m3["patch_sha256"] == m1["patch_sha256"]


def test_label_report_saw_the_run(tmp_path):
    tp, _, _ = _run(tmp_path)
    rep = json.loads((tp / "runs" / "run-01" / "label-report.json").read_text())
    # the mock produced one read + one edit; the stream was closed -> report is complete-case
    assert rep["provenance"]["reads_classified"] == 1 and rep["provenance"]["edits"] == 1
    assert rep["censoring"]["streams_closed"] == 1 and rep["censoring"]["streams_open"] == 0


def test_spec_sha_mismatch_is_rejected(tmp_path):
    import pytest
    repo, sha = _repo(tmp_path)
    spec = parse_spec(_spec(tmp_path, sha))
    with pytest.raises(ValueError):
        verify_spec(spec, "sha256:deadbeef")
