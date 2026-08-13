"""The reducer library. Each reducer preserves the decision-relevant invariants
(failures, root frames, exit code, paths/line numbers) and drops verbosity, then
returns the reduced text plus a handle to the full raw payload.
"""
from __future__ import annotations

import re

from .base import ReducedOutput, make_handle, tokens
from ..redact import redact

GREP_KEEP = 20
LOG_TAIL = 15
GENERIC_HEAD = 10
GENERIC_TAIL = 10

_FAIL = re.compile(r"(FAILED|ERROR\b|FAIL\b|✗|✘|AssertionError|Traceback|"
                   r"^E\s|Exception|panic:|--- FAIL)", re.I)
_SUMMARY = re.compile(r"(\d+\s+(failed|passed|error|skipped)|=+.*=+|Tests:|Ran \d+)", re.I)
_LOGLVL = re.compile(r"\b(ERROR|CRITICAL|FATAL|WARN|Exception|Traceback|panic)\b")


def _wrap(reducer, raw, kept, preserved, note=""):
    handle = make_handle(raw)                       # handle addresses the raw payload
    ok = all(any(p in k for k in kept) for p in preserved)   # check BEFORE redaction
    kept = [redact(k) for k in kept]                # never emit a secret in the summary
    reduced_text = "\n".join(kept + [f"[+ full output: {handle}]"])
    return ReducedOutput(reducer=reducer, reduced_text=reduced_text, handle=handle,
                         raw_tokens=tokens(raw), reduced_tokens=tokens(reduced_text),
                         invariants_ok=ok, preserved=[redact(p) for p in preserved], note=note)


def reduce_tests(raw: str, args: dict) -> ReducedOutput:
    lines = raw.splitlines()
    fails = [ln for ln in lines if _FAIL.search(ln)]
    summary = [ln for ln in lines if _SUMMARY.search(ln)]
    # invariant: every explicit FAILED line must survive
    must_keep = [ln for ln in lines if re.search(r"\bFAILED\b|--- FAIL", ln)]
    kept, seen = [], set()
    for ln in fails + summary[-3:]:
        if ln not in seen:
            kept.append(ln); seen.add(ln)
    if not kept:                       # all green: drop everything but the tally
        kept = [summary[-1]] if summary else ["(tests passed; no summary line found)"]
    return _wrap("tests", raw, kept, must_keep,
                 note="passing verbosity dropped; failures + summary kept")


def reduce_grep(raw: str, args: dict) -> ReducedOutput:
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    head = lines[:GREP_KEEP]
    omitted = max(0, len(lines) - GREP_KEEP)
    kept = head + ([f"... {omitted} more matches"] if omitted else [])
    return _wrap("grep", raw, kept, head, note=f"{len(lines)} matches, kept {len(head)}")


def reduce_git(raw: str, args: dict) -> ReducedOutput:
    kept, adds, dels = [], 0, 0
    for ln in raw.splitlines():
        if ln.startswith(("commit ", "Author:", "Date:", "diff --git", "+++", "---", "@@")):
            kept.append(ln)
        elif ln.startswith("+") and not ln.startswith("+++"):
            adds += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            dels += 1
    kept.append(f"[+{adds} / -{dels} changed lines]")
    heads = [ln for ln in kept if ln.startswith("diff --git")]
    return _wrap("git", raw, kept, heads, note="context lines dropped; headers + stat kept")


def reduce_logs(raw: str, args: dict) -> ReducedOutput:
    lines = raw.splitlines()
    important = [ln for ln in lines if _LOGLVL.search(ln)]
    must_keep = [ln for ln in lines if re.search(r"\b(ERROR|CRITICAL|FATAL)\b", ln)]
    tail = lines[-LOG_TAIL:]
    kept, seen = [], set()
    for ln in important + tail:
        if ln not in seen:
            kept.append(ln); seen.add(ln)
    return _wrap("logs", raw, kept, must_keep, note="ERROR/WARN + tail kept")


def reduce_generic(raw: str, args: dict) -> ReducedOutput:
    lines = raw.splitlines()
    if len(lines) <= GENERIC_HEAD + GENERIC_TAIL:
        return _wrap("generic", raw, lines, [], note="short; passthrough")
    omitted = len(lines) - GENERIC_HEAD - GENERIC_TAIL
    kept = lines[:GENERIC_HEAD] + [f"... {omitted} lines omitted ..."] + lines[-GENERIC_TAIL:]
    return _wrap("generic", raw, kept, [], note=f"{omitted} middle lines dropped")


REGISTRY = {"tests": reduce_tests, "grep": reduce_grep, "git": reduce_git,
            "logs": reduce_logs, "generic": reduce_generic}

_TEST_CMD = re.compile(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test|rspec|phpunit)\b")


def classify(tool_name: str | None, args: dict, kind: str | None) -> str:
    cmd = str((args or {}).get("command", ""))
    if kind == "test_result" or _TEST_CMD.search(cmd):
        return "tests"
    if kind == "search_result" or tool_name in ("Grep", "Glob"):
        return "grep"
    if tool_name == "Bash" and cmd.strip().startswith("git"):
        return "git"
    if kind == "log" or tool_name == "Bash":
        return "logs"
    return "generic"


def reduce_result(tool_name: str | None, args: dict, raw: str,
                  kind: str | None = None) -> ReducedOutput:
    return REGISTRY[classify(tool_name, args, kind)](raw, args or {})
