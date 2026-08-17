"""The reducer library. Each reducer preserves the decision-relevant invariants
(failures, root frames, exit code, paths/line numbers) and drops verbosity, then
returns the reduced text plus a handle to the full raw payload.
"""
from __future__ import annotations

import re
from collections import Counter

from .base import ReducedOutput, make_handle, tokens
from ..redact import redact

GREP_KEEP = 20
LOG_TAIL = 15
GENERIC_HEAD = 10
GENERIC_TAIL = 10

# B1.1 — search/listing reducer budget. The compact summary targets this many
# model-visible tokens; critical evidence (search diagnostics) and the recovery handle
# are NEVER dropped to meet it (evidence + recoverability outrank the budget target).
SEARCH_BUDGET_TOKENS = 256
FILE_TABLE_MAX = 12                  # named files in the "matches by file" rollup

# Search-tool diagnostics that are decision-relevant and must always survive: a search
# that could not read part of its scope is evidence, not verbosity.
_SEARCH_DIAG = re.compile(
    r"(: No such file or directory$|: Permission denied$|: Is a directory$|"
    r"^Binary file .* matches$|^grep: |^egrep: |^fgrep: |^rg: |^ripgrep: |^find: |^ls: )")

# grep -n / rg -n line:  path:lineno:content   ·   grep without -n:  path:content
_MATCH_WITH_LINENO = re.compile(r"^(.*?):(\d+):")
_MATCH_WITH_PATH = re.compile(r"^([^:]+):")

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


def _match_path(line: str) -> str:
    """The file a search-result line belongs to. Handles grep -n (`path:lineno:…`), grep
    without -n (`path:…`), and bare paths (find/ls). Falls back to the whole line."""
    m = _MATCH_WITH_LINENO.match(line) or _MATCH_WITH_PATH.match(line)
    return m.group(1) if m else line


def search_matched_paths(raw: str) -> list:
    """The file path of every non-diagnostic line in a search/listing output (order-preserving,
    may repeat). Feeds graphrank.path_scores so ranking keys line up with `_match_path`."""
    return [_match_path(ln) for ln in raw.splitlines()
            if ln.strip() and not _SEARCH_DIAG.search(ln)]


def reduce_search(raw: str, args: dict, *, budget_tokens: int = SEARCH_BUDGET_TOKENS,
                  representation: str = "search", path_scores: dict | None = None) -> ReducedOutput:
    """B1.1/B1.2 — budget-aware compaction for a search/listing output.

    Deterministic guarantees (all testable):
      * every kept match keeps its `path:lineno:` prefix VERBATIM — filenames and line
        numbers are never rewritten;
      * every search diagnostic (permission denied / no such file / binary match / tool
        error) survives, regardless of budget — it is decision-relevant evidence;
      * the model-visible summary targets `budget_tokens`; when matches are dropped to
        meet it, a per-file rollup names the highest-hit files so the *shape* of the
        result survives even when individual lines don't;
      * a `result://` recovery handle is always appended — full expansion is one call away.

    B1.2 — when `path_scores` is given (path → graph relevance, from graphrank), the matches
    RETAINED within budget are the highest-relevance ones (not merely the first), and the
    per-file rollup is ordered by relevance. `path_scores=None` is the B1.1 behavior exactly,
    so simple-vs-graph is a clean A/B on the same code path.
    """
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    diags, matches = [], []
    for ln in lines:
        (diags if _SEARCH_DIAG.search(ln) else matches).append(ln)

    by_file = Counter(_match_path(m) for m in matches)
    listing = representation == "path_listing"
    noun = "paths" if listing else "matches"
    header = f"{len(matches)} {noun} in {len(by_file)} file(s)"
    if diags:
        header += f"; {len(diags)} diagnostic(s)"

    handle = make_handle(raw)
    handle_tokens = tokens(f"[+ full output: {handle}]")
    # Must-keep skeleton: header + all diagnostics + the (reserved) handle line.
    must_keep = [header] + diags
    reserved = tokens("\n".join(must_keep)) + handle_tokens

    # Pre-build the truncation footer so its cost is RESERVED exactly (not guessed). Build the
    # rollup with the FINAL ordering up front — relevance-ranked when path_scores is given, else
    # count-ranked — so the reserved cost matches the footer actually emitted (a graph-ranked
    # rollup can list longer paths than the count-ranked one; reserving on the wrong ordering
    # could overshoot the budget).
    rollup = None
    if matches and not listing:
        if path_scores:
            order_files = sorted(by_file, key=lambda p: (-path_scores.get(p, 0.0), -by_file[p], p))
        else:
            order_files = [p for p, _ in by_file.most_common()]
        top = [(p, by_file[p]) for p in order_files[:FILE_TABLE_MAX]]
        rollup = "matches by file: " + ", ".join(f"{p}×{c}" for p, c in top)
        if len(by_file) > FILE_TABLE_MAX:
            rollup += f", +{len(by_file) - FILE_TABLE_MAX} more file(s)"
    if not matches:
        footer_reserve = 0
    elif listing:
        footer_reserve = tokens(f"... 0 more path(s) — full listing: {handle}")
    else:
        footer_reserve = tokens(rollup) + tokens(f"... 0 more match(es) across 0 file(s) — full: {handle}")
    room = max(0, budget_tokens - reserved - footer_reserve)

    # B1.2: retain the highest-relevance matches within budget. Ranked order is stable —
    # ties (and the no-scores case) fall back to original file order, so path_scores=None is
    # byte-identical to B1.1.
    order = list(range(len(matches)))
    if path_scores:
        order.sort(key=lambda i: (-path_scores.get(_match_path(matches[i]), 0.0), i))
    kept_idx, used = [], 0
    for i in order:
        t = tokens(matches[i]) + 1                # +1 for the newline join
        if used + t > room:
            break
        kept_idx.append(i)
        used += t
    kept_set = set(kept_idx)
    kept_matches = [matches[i] for i in kept_idx]
    dropped = [matches[i] for i in range(len(matches)) if i not in kept_set]

    body = list(must_keep) + kept_matches
    if dropped:                                   # footer only when we actually truncated
        if listing:
            body.append(f"... {len(dropped)} more path(s) — full listing: {handle}")
        else:                                     # `rollup` was already built in final order (reserved)
            files_with_dropped = len({_match_path(m) for m in dropped})
            body.append(rollup)
            body.append(f"... {len(dropped)} more match(es) across {files_with_dropped} file(s) — full: {handle}")

    note = (f"{len(matches)} {noun}, kept {len(kept_matches)}, budget {budget_tokens} tok"
            + (", graph-ranked" if path_scores else "")
            + (f", {len(diags)} diagnostic(s) preserved" if diags else ""))
    # Preserved evidence = the diagnostics (checked by _wrap before redaction). Filenames
    # of kept lines ride along in the kept lines themselves.
    return _wrap("search", raw, body, diags, note=note)


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
