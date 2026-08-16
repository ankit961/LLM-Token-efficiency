"""Corpus runner -- reproducible orchestration of ONE task through the frozen observation runtime.

NOT part of the frozen observation contract; this is experiment/product tooling that evolves freely.
Four separable components (each swappable), so the whole chain is testable at ZERO cost with mocks
before any real agent run or SWE-bench evaluation:

    TaskSetup    -- read+verify the locked run-NN spec, checkout the base_commit into an isolated worktree
    AgentBackend -- MockAgentBackend (deterministic) | ClaudeBackend (headless claude -p, later)
    Observation  -- obs-runtime-3a-v2.1 cr-hook -> per-run HookJournal -> closure -> label-report -> admission
    Evaluator    -- LocalStubEvaluator (defers) | OfficialSweBenchDockerEvaluator (linux, later)

A run produces one IMMUTABLE directory: manifest.json, journal.sqlite, label-report.json/.txt,
agent.patch, agent-result.json, evaluation.json, hashes.json. The agent run and the objective
evaluation are DELIBERATELY separate stages -- the agent never touches SWE-bench grading.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from . import labelreport
from .hookjournal import HookCapture, HookJournal

RUNTIME_TAG = "obs-runtime-3a-v2.1"
HOOK_SCHEMA = "0.4.1"
REPORT_SCHEMA = "label-report-0.2.1"


def _sha_bytes(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return _sha_bytes(fh.read())


def _sha_text(t: str) -> str:
    return _sha_bytes(t.encode("utf-8"))


# --------------------------------------------------------------------------- specs
@dataclass
class RunSpec:
    run_order: int
    task_id: str
    category: str
    base_commit: str
    repo_id: str
    spec_path: str
    spec_sha256: str
    problem_statement: str
    budget: str


def parse_spec(spec_path: str) -> RunSpec:
    """Parse a locked corpus/specs/run-NN.md into a RunSpec (and record its byte SHA for verification)."""
    text = open(spec_path, encoding="utf-8").read()

    def field_of(name, default=""):
        m = re.search(rf"^{name}:\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else default

    m = re.search(r"run-(\d+)", os.path.basename(spec_path))
    prompt = text.split("## Prompt (verbatim issue text the agent sees)", 1)
    problem = prompt[1].strip() if len(prompt) > 1 else ""
    return RunSpec(
        run_order=int(m.group(1)) if m else 0,
        task_id=field_of("task_id"),
        category=field_of("category"),
        base_commit=field_of("base_commit_sha"),
        repo_id=field_of("repo_id", "django/django"),
        spec_path=spec_path,
        spec_sha256=_sha_text(text),
        problem_statement=problem,
        budget=field_of("turn_or_walltime_budget"),
    )


def verify_spec(spec: RunSpec, expected_sha: Optional[str]) -> None:
    """The spec BYTES must match the plan's recorded task_spec_sha256 (a locked spec is immutable)."""
    if expected_sha and spec.spec_sha256 != expected_sha:
        raise ValueError(f"spec {spec.spec_path} sha {spec.spec_sha256} != locked {expected_sha}")


# --------------------------------------------------------------------------- task setup
class TaskSetup:
    """Checkout base_commit into a clean isolated worktree of a local repo mirror."""
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def worktree(self, base_commit: str, dest: str) -> str:
        if os.path.exists(dest):
            subprocess.run(["git", "-C", self.repo_path, "worktree", "remove", "--force", dest],
                           capture_output=True)
        subprocess.run(["git", "-C", self.repo_path, "worktree", "add", "-q", "--detach", dest, base_commit],
                       check=True, capture_output=True)
        return dest

    def cleanup(self, dest: str) -> None:
        subprocess.run(["git", "-C", self.repo_path, "worktree", "remove", "--force", dest],
                       capture_output=True)


# --------------------------------------------------------------------------- agent backends
@dataclass
class AgentResult:
    agent: str
    agent_version: str
    model: str
    patch: str                                   # unified diff produced by the agent (may be empty)
    result: dict                                 # the agent's own result json
    termination_reason: str                      # completed | budget_turns | budget_walltime | error
    budget_turns: Optional[int] = None
    budget_walltime: Optional[float] = None
    arm: str = "native"                          # native | semantic_directive | semantic_enforced


# Semantic Admission Experiment v1 arms (see docs/semantic-admission-experiment-v1.md once frozen).
# native            -- current baseline: no MCP, no steering. Arm A.
# semantic_directive-- SemanticFS MCP enabled + directive brief via --append-system-prompt; native
#                      tool calls remain fully available (fail-open, not a gate). Arm B.
# semantic_enforced -- RESERVED. Hard/enforced semantic admission. NOT implemented -- earned only
#                      after B's success + token results are reviewed (do not run yet).
ARMS = ("native", "semantic_directive", "semantic_enforced")


class AgentBackend(ABC):
    name = "abstract"

    @abstractmethod
    def run(self, worktree: str, spec: RunSpec, journal_db: str, settings_path: str) -> AgentResult:
        """Run the agent in `worktree` on spec.problem_statement, with cr-hook (settings_path) capturing
        to journal_db. Returns the patch + metadata. MUST NOT run any evaluation."""


class MockAgentBackend(AgentBackend):
    """Deterministic stand-in: writes a fixed synthetic journal (one read + one edit) via HookCapture
    and a fixed patch, so the whole orchestration is exercised at zero cost and reproducibly."""
    name = "mock"

    def __init__(self, model: str = "mock-model-1"):
        self.model = model

    def run(self, worktree, spec, journal_db, settings_path) -> AgentResult:
        # fixed clock + a deterministic hasher (v1 until the edit's post-hash -> v2, so the edit is a
        # verified_change, the read stays stable) make the journal bytes reproducible across runs.
        clock = iter(range(1, 10_000))
        versions = iter(["v1", "v1", "v1"] + ["v2"] * 1000)
        cap = HookCapture(HookJournal(journal_db),
                          hasher=lambda p: ("ok", next(versions)),
                          clock=lambda: next(clock))
        sid = "mock-session"
        cap.on_event({"hook_event_name": "SessionStart", "session_id": sid, "source": "startup"})
        cap.on_event({"hook_event_name": "UserPromptSubmit", "session_id": sid})
        cap.on_event({"hook_event_name": "PreToolUse", "session_id": sid, "cwd": worktree,
                      "tool_use_id": "r1", "tool_name": "Read",
                      "tool_input": {"file_path": os.path.join(worktree, "a.py")}})
        cap.on_event({"hook_event_name": "PostToolUse", "session_id": sid, "tool_use_id": "r1",
                      "tool_name": "Read", "tool_response": {"type": "text", "file": {"content": "x"}}})
        cap.on_event({"hook_event_name": "PostToolBatch", "session_id": sid, "prompt_id": "p1",
                      "tool_calls": [{"tool_use_id": "r1", "tool_name": "Read",
                                      "tool_response": "1\tdef f(): pass\n"}]})
        cap.on_event({"hook_event_name": "PreToolUse", "session_id": sid, "cwd": worktree,
                      "tool_use_id": "e1", "tool_name": "Edit",
                      "tool_input": {"file_path": os.path.join(worktree, "a.py")}})
        cap.on_event({"hook_event_name": "PostToolUse", "session_id": sid, "tool_use_id": "e1",
                      "tool_name": "Edit", "tool_response": {"type": "text"}})
        cap.on_event({"hook_event_name": "PostToolBatch", "session_id": sid, "prompt_id": "p2",
                      "tool_calls": [{"tool_use_id": "e1", "tool_name": "Edit",
                                      "tool_response": "updated"}]})
        cap.j.close()
        patch = ("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-def f(): pass\n+def f(): return 1\n")
        return AgentResult(agent=self.name, agent_version="0", model=self.model, patch=patch,
                           result={"reply": "DONE", "note": "mock deterministic run"},
                           termination_reason="completed", budget_turns=2, budget_walltime=0.0)


class ClaudeBackend(AgentBackend):
    """Real headless Claude Code (`claude -p`) run inside the worktree, cr-hook (settings_path) wired to
    the per-run journal. Budget wall-clock starts at the first agent request (this call), NOT during
    checkout/provisioning, per the plan's harness contract; SWE-bench grading is a SEPARATE stage.

    `arm` selects the Semantic Admission Experiment v1 condition (see ARMS). The task PROMPT is
    IDENTICAL across arms -- only extra CLI flags differ (--mcp-config, --append-system-prompt),
    per protocol ("same task prompt"). semantic_directive never disables native tools -- it only
    ADDS the SemanticFS MCP + a directive steering brief; a symbol_read failure or agent choice to
    ignore the brief falls back to native Read/Bash exactly as in the native arm (fail-open)."""
    name = "claude"

    def __init__(self, model: str = "sonnet", walltime_limit_s: int = 1200,
                 client_version: Optional[str] = None, clock=None,
                 arm: str = "native", codegraph_db: Optional[str] = None,
                 repo_id: Optional[str] = None):
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}; must be one of {ARMS}")
        if arm == "semantic_directive" and not codegraph_db:
            raise ValueError("arm='semantic_directive' requires codegraph_db "
                             "(the indexed code-graph backing the SemanticFS MCP)")
        self.model = model
        self.limit = walltime_limit_s
        self.client_version = client_version or self._version()
        self.clock = clock or __import__("time").time
        self.arm = arm
        self.codegraph_db = codegraph_db
        self.repo_id = repo_id

    @staticmethod
    def _version() -> str:
        try:
            return subprocess.run(["claude", "--version"], capture_output=True, text=True,
                                  timeout=15).stdout.strip() or "unknown"
        except Exception:      # noqa: BLE001
            return "unknown"

    def _mcp_config_path(self, run_dir: str, repo_id: str) -> str:
        from .install import cli_argv
        argv = cli_argv() + ["mcp", "--db", self.codegraph_db, "--repo", repo_id]
        # The agent runs with cwd=<target-repo worktree> (e.g. the django mirror), NOT this
        # package's own repo -- `python3 -m contextruntime.cli` only resolves via cwd-adds-to-
        # sys.path IF cwd happens to be this repo's root. contextruntime is not pip-installed, so
        # without this, the MCP server subprocess fails ModuleNotFoundError and silently never
        # starts -- the agent gets no read_symbol/etc and falls back to 100% native, invisibly.
        # Verified live: `python3 -c "import contextruntime"` from a foreign cwd fails without
        # PYTHONPATH, succeeds with it. Set explicitly rather than relying on the invoking shell's
        # environment, which the corpus batch script does not set.
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = {"mcpServers": {"contextruntime": {
            "command": argv[0], "args": argv[1:], "env": {"PYTHONPATH": pkg_root},
        }}}
        path = os.path.join(run_dir, "mcp-config.json")
        _write(path, json.dumps(cfg, indent=2))
        return path

    def _directive_brief(self, repo_id: str) -> str:
        from .policybrief import build_brief
        try:
            return build_brief(self.codegraph_db, repo_id)
        except Exception:      # noqa: BLE001 -- steering is advisory; never break the run
            return ""

    def run(self, worktree, spec, journal_db, settings_path) -> AgentResult:
        if self.arm == "semantic_enforced":
            raise NotImplementedError(
                "arm='semantic_enforced' is RESERVED -- hard/enforced semantic admission is not "
                "implemented. Per protocol it is earned only after semantic_directive's success and "
                "token results are reviewed. Use arm='native' or 'semantic_directive'.")

        # IDENTICAL across arms -- this is the one thing the protocol requires not to move.
        prompt = (spec.problem_statement + "\n\nWork in the repository at the fixed base commit; "
                  "implement a fix for the issue above. Reply DONE when finished.")
        argv = ["claude", "-p", prompt, "--settings", settings_path, "--model", self.model,
                "--permission-mode", "bypassPermissions"]

        steering = {"mcp_enabled": False, "brief_included": False, "brief_chars": 0, "brief_version": None}
        if self.arm == "semantic_directive":
            run_dir = os.path.dirname(settings_path)
            repo_id = self.repo_id or spec.repo_id
            mcp_path = self._mcp_config_path(run_dir, repo_id)
            argv += ["--mcp-config", mcp_path]
            steering["mcp_enabled"] = True
            brief = self._directive_brief(repo_id)
            if brief:
                argv += ["--append-system-prompt", brief]
                steering["brief_included"] = True
                steering["brief_chars"] = len(brief)
                from .policybrief import BRIEF_VERSION
                steering["brief_version"] = BRIEF_VERSION

        t0 = self.clock()
        term, tail, rc = "completed", "", 0
        try:
            proc = subprocess.run(argv, cwd=worktree, capture_output=True, text=True, timeout=self.limit)
            rc, tail = proc.returncode, (proc.stdout or "")[-2000:]
            term = "completed" if rc == 0 else "error"
        except subprocess.TimeoutExpired:
            term, rc = "budget_walltime", None
        walltime = round(self.clock() - t0, 3)
        diff = subprocess.run(["git", "-C", worktree, "diff"], capture_output=True, text=True).stdout
        return AgentResult(agent=self.name, agent_version=self.client_version, model=self.model,
                           patch=diff, result={"returncode": rc, "stdout_tail": tail, "arm": self.arm,
                                               "steering": steering},
                           termination_reason=term, budget_turns=None, budget_walltime=walltime,
                           arm=self.arm)


# --------------------------------------------------------------------------- evaluators
@dataclass
class EvalResult:
    status: str                                  # resolved | unresolved | eval_deferred | error
    resolved: Optional[bool]
    detail: dict = field(default_factory=dict)


class Evaluator(ABC):
    name = "abstract"

    @abstractmethod
    def evaluate(self, spec: RunSpec, patch: str) -> EvalResult:
        ...


class LocalStubEvaluator(Evaluator):
    """No SWE-bench grading here -- the agent run and objective evaluation are separate stages. Records
    the patch as pending for the OfficialSweBenchDockerEvaluator (linux)."""
    name = "local_stub"

    def evaluate(self, spec, patch) -> EvalResult:
        return EvalResult(status="eval_deferred", resolved=None,
                          detail={"note": "run OfficialSweBenchDockerEvaluator on linux/amd64",
                                  "patch_sha256": _sha_text(patch), "task_id": spec.task_id})


# --------------------------------------------------------------------------- runner
class CorpusRunner:
    def __init__(self, repo_path: str, runs_dir: str, agent: AgentBackend, evaluator: Evaluator,
                 runtime_sha: str = "", runtime_tag: str = RUNTIME_TAG,
                 crhook_cmd: str = "contextruntime cr-hook"):
        self.setup = TaskSetup(repo_path)
        self.runs_dir = runs_dir
        self.agent = agent
        self.evaluator = evaluator
        self.runtime_sha = runtime_sha
        self.runtime_tag = runtime_tag
        self.crhook_cmd = crhook_cmd            # the FROZEN cr-hook binary (from obs-runtime-3a-v2.1)

    def run_one(self, spec: RunSpec, *, expected_spec_sha: Optional[str] = None,
                start_time: str = "", end_time: str = "", in_corpus: bool = True) -> dict:
        verify_spec(spec, expected_spec_sha)
        run_dir = os.path.join(self.runs_dir, f"run-{spec.run_order:02d}")
        os.makedirs(run_dir, exist_ok=True)
        wt = os.path.join(run_dir, "worktree")
        journal_db = os.path.join(run_dir, "journal.sqlite")
        for p in (journal_db,):
            if os.path.exists(p):
                os.remove(p)

        self.setup.worktree(spec.base_commit, wt)
        settings_path = self._write_hook_settings(run_dir, journal_db)
        try:
            agent_res = self.agent.run(wt, spec, journal_db, settings_path)
        finally:
            self.setup.cleanup(wt)

        # Observation: the run is a controlled task -> its stream is closed at completion.
        rows = HookJournal(journal_db)
        streams = {r["stream_key"] for r in rows.tool_events() if r["stream_key"]}
        rows.close()
        manifest_closure = {"streams": [{"stream_key": sk, "closed": True,
                                         "closure_reason": "controlled_run_completed"} for sk in streams]}
        report = labelreport.build_report(journal_db, manifest=manifest_closure,
                                          runtime_commit_sha=self.runtime_sha,
                                          client_version=agent_res.agent_version)
        eval_res = self.evaluator.evaluate(spec, agent_res.patch)

        # write immutable artifacts
        _write(os.path.join(run_dir, "label-report.json"), labelreport.report_json(report))
        _write(os.path.join(run_dir, "label-report.txt"), labelreport.format_text(report))
        _write(os.path.join(run_dir, "agent.patch"), agent_res.patch)
        _write(os.path.join(run_dir, "agent-result.json"), json.dumps(agent_res.result, indent=2, sort_keys=True))
        _write(os.path.join(run_dir, "evaluation.json"),
               json.dumps({"status": eval_res.status, "resolved": eval_res.resolved,
                           "detail": eval_res.detail, "evaluator": self.evaluator.name}, indent=2, sort_keys=True))
        artifacts = ["journal.sqlite", "label-report.json", "agent.patch", "agent-result.json", "evaluation.json"]
        hashes = {a: _sha_file(os.path.join(run_dir, a)) for a in artifacts}
        _write(os.path.join(run_dir, "hashes.json"), json.dumps(hashes, indent=2, sort_keys=True))

        admission = report["admission"]
        integrity = report["capture_integrity"]
        manifest = {
            "run_order": spec.run_order, "task_id": spec.task_id, "task_spec_sha256": spec.spec_sha256,
            "category": spec.category, "base_commit": spec.base_commit, "repo_id": spec.repo_id,
            "in_corpus": in_corpus,
            "runtime_tag": self.runtime_tag, "runtime_sha": self.runtime_sha,
            "hook_schema": HOOK_SCHEMA, "report_schema": REPORT_SCHEMA,
            "agent": agent_res.agent, "agent_version": agent_res.agent_version, "model": agent_res.model,
            "arm": agent_res.arm,
            "start_time": start_time, "end_time": end_time,
            "termination_reason": agent_res.termination_reason,
            "budget_turns": agent_res.budget_turns, "budget_walltime": agent_res.budget_walltime,
            "journal_sha256": hashes["journal.sqlite"], "patch_sha256": _sha_text(agent_res.patch),
            "evaluation_status": eval_res.status,
            # a quick health snapshot (the full evidence is in label-report.json)
            "capture_errors": integrity["errors"], "pending_tools": integrity["pending_tools"],
            "pre_capture_rate": integrity["pre_capture_rate"],
            "canonical_admissible": admission["canonical_admissible"],
            "artifact_hashes": hashes,
        }
        _write(os.path.join(run_dir, "manifest.json"), json.dumps(manifest, indent=2, sort_keys=True))
        return manifest

    def _write_hook_settings(self, run_dir: str, journal_db: str) -> str:
        """cr-hook settings wiring the FROZEN cr-hook to this run's journal (used by a real agent
        backend; the mock writes the journal directly)."""
        crhook = f"{self.crhook_cmd} --db {journal_db}"

        def grp(matcher=True):
            g = {"hooks": [{"type": "command", "command": crhook}]}
            if matcher:
                g["matcher"] = ""
            return [g]
        settings = {"hooks": {"SessionStart": grp(False), "UserPromptSubmit": grp(False),
                              "PreToolUse": grp(), "PostToolUse": grp(), "PostToolUseFailure": grp(),
                              "PostToolBatch": grp(False), "SubagentStart": grp()}}
        path = os.path.join(run_dir, "cr-hook-settings.json")
        _write(path, json.dumps(settings, indent=2))
        return path


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
