#!/usr/bin/env python3
"""B6 — native (no-docker) SWE-bench grading for modern django tasks on this machine.

Modern django (2023+ base commits) imports and runs its test suite under python3.11 directly
(`PYTHONPATH=<worktree> python3.11 tests/runtests.py <labels> --parallel=1`), so real
FAIL_TO_PASS / PASS_TO_PASS grading needs no container:

  1. worktree at `base_commit`
  2. apply `test_patch` ONLY (it adds/extends the tests that define FAIL_TO_PASS)
  3. FAIL_TO_PASS must FAIL   → confirms the bug is present and the tests bite
  4. apply the model's patch (or the gold `patch` when validating the task itself)
  5. FAIL_TO_PASS must PASS and PASS_TO_PASS must stay green

`validate_task` runs 2–4 on the GOLD patch — a zero-quota proof that this machine can grade the
instance at all; only validated instances are eligible for the live A/B.
"""
from __future__ import annotations

import os
import re
import subprocess

PY = "python3.11"
_NAME = re.compile(r"^(?P<meth>.+) \((?P<cls>[\w.]+)\)$", re.S)
_WORD = re.compile(r"^[\w.]+$")


def to_label(test_name: str) -> str:
    """SWE-bench test names → runtests labels. THREE formats exist:
    old:        'test_x (module.Class)'          → 'module.Class.test_x'
    new:        'test_x (module.Class.test_x)'   → 'module.Class.test_x'  (parenthetical complete)
    docstring:  'Some sentence… (module.Class.test_x)' → 'module.Class.test_x'
    Already-dotted names pass through."""
    m = _NAME.match(test_name.strip())
    if m:
        cls, meth = m.group("cls"), m.group("meth").strip()
        if not _WORD.match(meth):                        # docstring-named: the parenthetical IS the id
            return cls
        return cls if cls.endswith("." + meth) else f"{cls}.{meth}"
    return test_name.strip()


def apply_patch(wt: str, patch_text: str) -> bool:
    p = subprocess.run(["git", "-C", wt, "apply", "--whitespace=nowarn", "-"],
                       input=patch_text, capture_output=True, text=True)
    return p.returncode == 0


def run_tests(wt: str, labels, *, timeout=600):
    """Run labels together; returns (all_passed, per-run summary line). runtests exits non-zero on any
    failure/error, which is exactly the pass/fail signal grading needs."""
    if not labels:
        return True, "no tests"
    env = dict(os.environ, PYTHONPATH=wt)
    p = subprocess.run([PY, "tests/runtests.py", "--parallel=1", "-v0", *labels],
                       cwd=wt, env=env, capture_output=True, text=True, timeout=timeout)
    tail = (p.stdout + p.stderr).strip().splitlines()[-2:]
    return p.returncode == 0, " | ".join(t.strip() for t in tail)[:200]


def resolvable(names):
    """SWE-bench data quirk: some entries are BARE DOCSTRINGS with no test id at all — unresolvable
    from the string. Grade on the resolvable ones and report how many were skipped."""
    keep = [t for t in names if "(" in t]
    return [to_label(t) for t in keep], len(names) - len(keep)


def grade(wt: str, task: dict, model_patch: str = None, *, p2p_cap=40):
    """Grade a worktree that already contains the MODEL's changes (or apply `model_patch` /
    the gold patch first). Returns the grading record."""
    if model_patch is not None and not apply_patch(wt, model_patch):
        return {"patch_applied": False, "f2p_pass": False, "p2p_pass": False}
    f2p, f2p_skip = resolvable(task["FAIL_TO_PASS"])
    p2p, p2p_skip = resolvable(task["PASS_TO_PASS"])
    p2p = p2p[:p2p_cap]
    ok_f, s_f = run_tests(wt, f2p)
    ok_p, s_p = run_tests(wt, p2p)
    return {"patch_applied": True, "f2p_pass": ok_f, "p2p_pass": ok_p, "p2p_checked": len(p2p),
            "f2p_skipped_unresolvable": f2p_skip, "p2p_skipped_unresolvable": p2p_skip,
            "f2p_note": s_f, "p2p_note": s_p, "success": ok_f and ok_p}


def validate_task(mirror: str, task: dict, workdir: str):
    """Zero-quota proof this machine can grade the instance: pre-fix F2P fails; gold patch passes."""
    wt = os.path.join(workdir, f"validate-{task['instance_id']}")
    subprocess.run(["git", "-C", mirror, "worktree", "remove", "--force", wt], capture_output=True)
    subprocess.run(["git", "-C", mirror, "worktree", "add", "--detach", wt, task["base_commit"]],
                   capture_output=True, text=True)
    rec = {"instance_id": task["instance_id"]}
    try:
        if not apply_patch(wt, task["test_patch"]):
            rec["error"] = "test_patch failed to apply"
            return rec
        f2p, _skip = resolvable(task["FAIL_TO_PASS"])
        pre_ok, pre_note = run_tests(wt, f2p)
        rec["pre_f2p_fails"] = not pre_ok                      # must be True (bug present)
        rec["pre_note"] = pre_note
        g = grade(wt, task, model_patch=task["patch"])
        rec.update({"gold_" + k: v for k, v in g.items()})
        rec["valid"] = rec.get("pre_f2p_fails") and g.get("success", False)
    except subprocess.TimeoutExpired:
        rec["error"] = "test run timeout"
    finally:
        subprocess.run(["git", "-C", mirror, "worktree", "remove", "--force", wt], capture_output=True)
    return rec
