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


def _floor() -> int:
    """Min raw tokens before a search/listing output is worth reducing (CR_REDUCE_FLOOR override,
    default MIN_REDUCE_TOKENS). Mirrors CR_REDUCE_BUDGET so the offline replay's floor grid maps to
    real, deployable configs — a lower floor is still subject to the recovery invariant (replace
    only when the CAS confirms exact recovery)."""
    try:
        f = int(os.environ.get("CR_REDUCE_FLOOR", ""))
        return f if f > 0 else MIN_REDUCE_TOKENS
    except (TypeError, ValueError):
        return MIN_REDUCE_TOKENS


def _graph_scores(event: dict, raw: str, decision) -> dict:
    """B1.2 — best-effort graph relevance per matched path, for ranking retention. TOTALLY
    fail-open: any missing piece (no CR_GRAPH_DB, no session TOUCHED anchors, schema mismatch,
    any error) returns {} and the reducer falls back to plain file order (== B1.1). Graph
    ranking NEVER changes which representations are reduced or whether recovery is required —
    it only reorders which matches are kept within the same budget, so it cannot affect safety.

    Live MENTIONS are intentionally empty: the journal is metadata-only (no prompt text), so
    anchors come from TOUCHED alone. Ranking therefore only engages once the session has read/
    edited some files — before that, simple order, honestly."""
    try:
        graph_db = os.environ.get("CR_GRAPH_DB")
        repo_id = os.environ.get("CR_REPO_ID")
        if not (graph_db and repo_id and os.path.exists(graph_db)):
            return {}
        from ..store import GraphStore
        from . import graphrank
        from .library import search_matched_paths
        touched = graphrank.touched_from_journal(
            os.environ.get("CR_JOURNAL_DB"), event.get("session_id"))
        ws = graphrank.WorkingSet(touched, frozenset())
        if ws.empty:
            return {}
        store = GraphStore(graph_db)
        try:
            return graphrank.path_scores(store, repo_id, search_matched_paths(raw), ws)
        finally:
            store.close()
    except Exception:                       # noqa: BLE001 — ranking is best-effort, never blocks
        return {}


def _passthrough(note: str = "") -> int:
    if note:
        print(note, file=sys.stderr)
    print("{}")            # empty result => leave tool output untouched
    return 0


def _raw_text(resp):
    """Return (raw_text, shape). ONLY three shapes are supported for replacement:
      str · dict with a string `stdout` · dict with a string `content`.
    Anything else (e.g. a content-block LIST, or an unfamiliar schema) returns
    ('unsupported') so the caller passes through UNCHANGED — never json.dumps a live
    response and substitute it (that would violate both schema-perfection and
    uncertain⇒pass-through)."""
    if isinstance(resp, str):
        return resp, "str"
    if isinstance(resp, dict):
        if isinstance(resp.get("stdout"), str):
            return resp["stdout"], "stdout"
        if isinstance(resp.get("content"), str):
            return resp["content"], "content"
    return None, "unsupported"


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
    if shape == "unsupported":
        return _passthrough()          # unknown response schema — never rewrite (uncertain ⇒ pass through)
    raw_tok = tokens(raw)
    if raw_tok < _floor():
        return _passthrough()          # too small to be worth an envelope

    # (1b) B1.2 graph-informed ranking (best-effort, fail-open → simple order).
    scores = _graph_scores(event, raw, decision)
    red = reduce_search(raw, args, budget_tokens=_budget(),
                        representation=decision.representation or "search",
                        path_scores=scores or None)
    if not red.invariants_ok:
        return _passthrough("[contextreduce] invariant check failed — passing raw through")
    # Never replace with something at least as large: reduce_search preserves ALL diagnostics
    # + header + rollup + handle, so a diagnostic-heavy output can end up BIGGER than the raw.
    # Invariant: replace ⇒ T_replacement < T_native. (saved_tokens' max(0,..) would otherwise
    # report 0 while the context actually grew.)
    if red.reduced_tokens >= raw_tok:
        return _passthrough(f"[contextreduce] reduction not beneficial "
                            f"({red.reduced_tokens} >= {raw_tok} tok) — passing through")

    # (3) Version gate + (5) enforce gate. Both must hold to replace what the model sees.
    # RUNTIME-gated and FAIL-SAFE: enforcement requires a CONFIRMED LIVE `claude --version` (cached
    # probe). If the live version can't be determined, we are uncertain, so we DO NOT enforce — the
    # value baked into the hook command at install time may be stale after a client auto-update, and
    # "does nothing when uncertain" forbids trusting it. Where the probe can't reach claude but the
    # operator can vouch for the version, they assert it deliberately via CR_LIVE_CLIENT_VERSION.
    baked_version = os.environ.get("CR_CLIENT_VERSION")     # install-time; informational only now
    live_version = doctor.live_client_version()
    client_version = live_version
    version_ok = live_version is not None and doctor.output_replacement_confirmed(live_version)
    enforce = os.environ.get("CR_REDUCE_MODE") == "enforce"
    will_replace = enforce and version_ok

    cas = None
    if will_replace:
        # (2) STRICT recovery: replace ONLY after the live CAS confirms the COMPLETE payload
        # is persisted and recoverable. A failed OR bounded/truncated write => do not reduce;
        # pass the raw output through unchanged. This closes the dead-/partial-handle hole:
        # we never reduce below the floor unless recovery is genuinely available.
        cas = livecas.put_confirmed(raw, reducer=red.reducer,
                                    representation=decision.representation or "")
        if not (cas.persisted and cas.exact):
            will_replace = False

    # (7) Durable decision record for offline replay / live observability.
    livecas.log_decision({
        "tool": decision.tool, "representation": decision.representation,
        "reducer": red.reducer, "reason": decision.reason,
        "raw_tokens": red.raw_tokens, "reduced_tokens": red.reduced_tokens,
        "saved_tokens": red.saved_tokens, "ratio": round(red.ratio, 4),
        "handle": red.handle, "enforced": will_replace, "version_ok": version_ok,
        "client_version": client_version, "live_version": live_version,
        "baked_version": baked_version, "invariants_ok": red.invariants_ok,
        "cas_persisted": bool(cas and cas.persisted),
        "cas_exact": bool(cas and cas.exact),
        "cas_truncated": bool(cas and cas.truncated),
        "cas_redacted": bool(cas and cas.redacted),
        "graph_ranked": bool(scores),
        "graph_scored_paths": len(scores),
    })

    if not will_replace:
        if not enforce:
            why = "observe mode (CR_REDUCE_MODE≠enforce)"
        elif not version_ok:
            why = (f"live client version {live_version!r} not confirmed for output replacement"
                   if live_version is not None
                   else "live client version could not be determined — refusing to enforce on a "
                        "possibly-stale baked value (set CR_LIVE_CLIENT_VERSION to assert it)")
        else:
            why = (f"live CAS could not confirm complete recovery "
                   f"({cas.note if cas else 'no write'}) — passing raw through")
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
