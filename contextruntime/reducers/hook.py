"""PostToolUse hook handler — B1.0 transparent reducer (Transparent Reduction Contract v0.1).

Reads a PostToolUse event on stdin and, IF the call is a prospectively-recognizable
search/listing output AND the client version is confirmed AND enforcement is enabled,
returns a compact replacement the model will see — plus a `result://` handle that is
genuinely recoverable from the live CAS.

Invariants enforced here (all testable, all fail-open):
  1. PROSPECTIVE-ONLY gate (`gate.route`): only `search`/`path_listing` representations
     are ever touched; native Read, file/git_blob materialization, execution, mixed or
     uncertain Bash, and every non-search tool PASS THROUGH unchanged.
  2. RECOVERABLE handle: the raw payload is written (redacted, bounded) to the live CAS
     BEFORE the compact summary is emitted, so `context_expand(result://…)` resolves it.
  3. VERSION-GATED: replacement fires only on a live-confirmed client version
     (`CR_CLIENT_VERSION` ∈ doctor allowlist); an unknown/missing version fails safe.
  4. SCHEMA-PERFECT: preserve the tool_response shape (str → reduce string; dict → reduce
     the stdout/content field). A malformed replacement would abort the turn.
  5. OBSERVE BY DEFAULT: `CR_REDUCE_MODE=enforce` is required to actually replace;
     otherwise the would-be saving is recorded and the raw output passes through.
  6. FAIL-OPEN: ANY error (or absent input) → print {} and exit 0.

Wire in settings.json:  "PostToolUse": [{ "matcher": "Grep|Glob|Bash",
                          "hooks": [{ "type": "command",
                          "command": "python3 -m contextruntime.reducers.hook" }] }]
"""
from __future__ import annotations

import json
import os
import sys

from .base import tokens
from .gate import route
from .library import reduce_search, SEARCH_BUDGET_TOKENS
from . import livecas
from .. import doctor

# A gated call this small isn't worth a handle+summary envelope — pass it through.
MIN_REDUCE_TOKENS = 400


def _budget() -> int:
    """Model-visible token budget for the compact summary (CR_REDUCE_BUDGET override)."""
    try:
        b = int(os.environ.get("CR_REDUCE_BUDGET", ""))
        return b if b > 0 else SEARCH_BUDGET_TOKENS
    except (TypeError, ValueError):
        return SEARCH_BUDGET_TOKENS


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


def handle(event: dict) -> int:
    tool = event.get("tool_name")
    args = event.get("tool_input") or {}
    resp = event.get("tool_response", event.get("tool_result"))
    if resp is None:
        return _passthrough()

    # (1) Prospective-only gate — the ONLY decision the reducer is allowed to make.
    decision = route(tool, args)
    if decision.passthrough:
        return _passthrough()          # silent: passthrough is the frequent, expected case

    raw, shape = _raw_text(resp)
    raw_tok = tokens(raw)
    if raw_tok < MIN_REDUCE_TOKENS:
        return _passthrough()          # too small to be worth an envelope

    red = reduce_search(raw, args, budget_tokens=_budget(),
                        representation=decision.representation or "search")
    if not red.invariants_ok:
        return _passthrough("[contextreduce] invariant check failed — passing raw through")

    # (3) Version gate + (5) enforce gate. Both must hold to replace what the model sees.
    client_version = os.environ.get("CR_CLIENT_VERSION")
    version_ok = doctor.output_replacement_confirmed(client_version)
    enforce = os.environ.get("CR_REDUCE_MODE") == "enforce"
    will_replace = enforce and version_ok

    if will_replace:
        # (2) Make the handle recoverable BEFORE emitting the summary that references it.
        livecas.put(raw, reducer=red.reducer, representation=decision.representation or "")

    # (7) Durable decision record for offline replay / live observability.
    livecas.log_decision({
        "tool": decision.tool, "representation": decision.representation,
        "reducer": red.reducer, "reason": decision.reason,
        "raw_tokens": red.raw_tokens, "reduced_tokens": red.reduced_tokens,
        "saved_tokens": red.saved_tokens, "ratio": round(red.ratio, 4),
        "handle": red.handle, "enforced": will_replace, "version_ok": version_ok,
        "client_version": client_version, "invariants_ok": red.invariants_ok,
    })

    if not will_replace:
        why = ("observe mode (CR_REDUCE_MODE≠enforce)" if not enforce
               else f"client version {client_version!r} not confirmed for output replacement")
        return _passthrough(
            f"[contextreduce] observe: {red.reducer} would save {red.saved_tokens} tok "
            f"({100*(1-red.ratio):.0f}%) on a {decision.representation} result — {why}")

    # (4) schema-perfect replacement
    if shape == "str":
        new_output = red.reduced_text
    else:
        new_output = dict(resp)
        new_output[shape] = red.reduced_text
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        # NOTE: exact field name is version-gated — only emitted on a confirmed version.
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
