#!/usr/bin/env python3
"""B2 bash/test-output residency go/no-go — the portfolio's next lever after file compaction. ZERO quota.

Bash/execution output (test runs) is the other big prefix component, and unlike file reads it is NOT
an edit target — so it dodges the edit-recall wall that stopped B2.1/B2.2. The reducer is the existing
`reduce_tests` (keep every FAILED line + the summary tally, drop passing verbosity), which is safe by
construction: the decision-critical evidence (failures/errors) is the preserved invariant.

Measures, over the native Step-7 sessions:
  * SHARE — bash tokens as a fraction of all accumulated tool-output tokens; test-like share.
  * CEILING — compounding-aware (a test output at turn t is cache-read every later turn) whole-session
    T_total saving from summarizing the passing tail of test-like bash outputs, beneficial-only.
"""
from __future__ import annotations

import json
import os
import re

from contextruntime.reducers.base import tokens as _tok
from contextruntime.reducers.library import reduce_tests
from corpus.prefix_decomposition import accumulated_composition, category, transcript_for_worktree

# a bash output is "test-like" (summarizable) if it carries a test-runner signature
_TEST_LIKE = re.compile(
    r"(\d+\s+(passed|failed|error|skipped)|test session starts|collected \d+ item|Ran \d+ test|"
    r"=+\s*(FAILURES|ERRORS|short test summary)|^\s*(PASSED|FAILED)\b|::\w|"
    r"Creating test database|Destroying test database|System check identified|^OK$|^FAILED \()",
    re.I | re.M)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_test_like(output: str) -> bool:
    return bool(_TEST_LIKE.search(output or ""))


def parse_bash_outputs(transcript_path: str):
    """(outputs, total_turns): outputs = [(turn, content)] for every Bash tool result; total_turns is
    the SESSION's assistant-turn count (so the compounding window uses session length, not the last
    bash turn)."""
    out, uses, turn = [], {}, 0
    for line in open(transcript_path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:      # noqa: BLE001
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if rec.get("type") == "assistant" and isinstance(content, list):
            if msg.get("usage"):
                turn += 1
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses[b.get("id")] = b.get("name")
        elif rec.get("type") == "user" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and uses.get(b.get("tool_use_id")) == "Bash":
                    out.append((turn, _text(b.get("content"))))
    return out, max(turn, 1)


def bash_reduction_ceiling(transcript_path: str, t_total, *, floor: int = 200) -> dict:
    """Compounding-aware whole-session T_total saving from summarizing test-like bash outputs."""
    outs, total_turns = parse_bash_outputs(transcript_path)
    bash_tok = testlike_tok = saving = fired = 0
    for (t, content) in outs:
        rt = _tok(content)
        bash_tok += rt
        if not is_test_like(content) or rt < floor:
            continue
        testlike_tok += rt
        red = reduce_tests(content, {})
        if red.reduced_tokens >= rt:                 # beneficial-only
            continue
        fired += 1
        saving += (rt - red.reduced_tokens) * max(total_turns - t, 0)   # compounds over later turns
    tt = t_total or 0
    return {"t_total": tt, "bash_tokens": bash_tok, "testlike_tokens": testlike_tok,
            "fired": fired, "compounded_saving": round(saving),
            "pct_of_T_total": round(saving / tt * 100, 2) if tt else None}


def go_no_go(results_json: str, *, arm: str = "A_native", floor: int = 200) -> dict:
    res = json.load(open(results_json))
    rows, bash_tot, testlike_tot, toolout_tot = [], 0, 0, 0
    for key, m in res.items():
        if f"|{arm}|" not in key or not isinstance(m, dict) or "error" in m:
            continue
        tp, tt = m.get("transcript"), m.get("T_total")
        if not (tp and tt and os.path.exists(tp)):
            continue
        r = bash_reduction_ceiling(tp, tt, floor=floor)
        rows.append(r["pct_of_T_total"])
        bash_tot += r["bash_tokens"]; testlike_tot += r["testlike_tokens"]
        comp = accumulated_composition(tp)
        toolout_tot += comp["tool_outputs_total"]

    def _mean(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 2) if xs else None
    return {"n": len([x for x in rows if x is not None]),
            "bash_share_of_tool_output": round(bash_tot / toolout_tot, 4) if toolout_tot else None,
            "testlike_share_of_bash": round(testlike_tot / bash_tot, 4) if bash_tot else None,
            "mean_pct_of_T_total": _mean(rows), "max_pct_of_T_total": (round(max([x for x in rows if x is not None]), 2) if any(x is not None for x in rows) else None),
            "per_session_pct": rows}


def _main(argv) -> None:
    r = go_no_go(argv[1])
    print("=== B2 bash/test-output residency GO/NO-GO ===")
    print(f"  native sessions: {r['n']}")
    print(f"  bash share of tool output: {r['bash_share_of_tool_output']}  "
          f"| test-like share of bash: {r['testlike_share_of_bash']}")
    print(f"  compounding-aware whole-session T_total saving (keep-failures/summarize-passes):")
    print(f"    mean {r['mean_pct_of_T_total']}%   max {r['max_pct_of_T_total']}%")
    print(f"  per-session %: {r['per_session_pct']}")


if __name__ == "__main__":
    import sys
    _main(sys.argv)
