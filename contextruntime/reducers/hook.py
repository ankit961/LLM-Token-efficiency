"""PostToolUse hook handler (design v1.2 §7, §17; implementation chapter).

Reads a PostToolUse event on stdin and, IF the client supports output replacement,
returns a reduced tool result the model will see, preserving the response shape.

Invariants enforced here:
  - version-gated: only emit a replacement when the Doctor confirms the capability;
    otherwise no-op and say so on stderr (degrade loudly, never silent).
  - schema-perfect: preserve the tool_response shape (dict -> reduce stdout field;
    string -> reduce string). A malformed replacement would abort the turn.
  - fail-open: ANY error (or a hung/absent daemon) -> print {} and exit 0.
  - observe by default: CR_REDUCE_MODE=enforce is required to actually replace;
    otherwise it reports the would-be saving on stderr and emits {}.

Wire in settings.json:  "PostToolUse": [{ "matcher": "Read|Grep|Bash",
                          "hooks": [{ "type": "command",
                          "command": "python3 -m contextruntime.reducers.hook" }] }]
"""
from __future__ import annotations

import json
import os
import sys

from .base import should_reduce, tokens
from .library import classify, reduce_result
from ..ingest import _categorize_tool  # (name) -> (kind, provenance)


def _passthrough(note: str = "") -> int:
    if note:
        print(note, file=sys.stderr)
    print("{}")            # empty result => leave tool output untouched
    return 0


def _raw_text(resp) -> tuple[str, str]:
    """Return (raw_text, shape) where shape in {'str','stdout','content'}."""
    if isinstance(resp, str):
        return resp, "str"
    if isinstance(resp, dict):
        if isinstance(resp.get("stdout"), str):
            return resp["stdout"], "stdout"
        if isinstance(resp.get("content"), str):
            return resp["content"], "content"
    return json.dumps(resp, default=str), "str"


def _capability_ok() -> bool:
    """Version gate. The Phase-0b Doctor is a stub (unverified), so replacement is
    OFF unless the operator asserts it via CR_OUTPUT_REPLACEMENT=1 (they confirmed
    it on their client)."""
    from .. import doctor as doctor_mod
    prof = doctor_mod.probe()
    if os.environ.get("CR_OUTPUT_REPLACEMENT") == "1":
        return True
    return prof.capabilities.get("post_tool_use_output_replacement") == "yes"


def handle(event: dict) -> int:
    tool = event.get("tool_name")
    args = event.get("tool_input") or {}
    resp = event.get("tool_response", event.get("tool_result"))
    if resp is None:
        return _passthrough()

    raw, shape = _raw_text(resp)
    kind, _prov = _categorize_tool(tool or "?")
    ok, reason = should_reduce(kind, tokens(raw))
    if not ok:
        return _passthrough()          # silent for retention skips (expected, frequent)

    red = reduce_result(tool, args, raw, kind=kind)
    if not red.invariants_ok:
        return _passthrough("[contextreduce] invariant check failed — passing raw through")

    enforce = os.environ.get("CR_REDUCE_MODE") == "enforce"
    if not (_capability_ok() and enforce):
        return _passthrough(
            f"[contextreduce] observe: {red.reducer} would save "
            f"{red.saved_tokens} tok ({100*(1-red.ratio):.0f}%); "
            f"{'enable CR_REDUCE_MODE=enforce + confirmed output replacement' if enforce else 'observe mode'}")

    # schema-perfect replacement
    if shape == "str":
        new_output = red.reduced_text
    else:
        new_output = dict(resp)
        new_output[shape] = red.reduced_text
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        # NOTE: exact field name is version-gated — verify at runtime.
        "updatedToolOutput": new_output,
    }}))
    return 0


def main(argv=None) -> int:
    try:
        data = sys.stdin.read()
        event = json.loads(data) if data.strip() else {}
    except Exception:                  # noqa: BLE001 — fail open on bad input
        return _passthrough()
    try:
        return handle(event)
    except Exception as e:             # noqa: BLE001 — fail open on any error
        return _passthrough(f"[contextreduce] error, passing through: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
