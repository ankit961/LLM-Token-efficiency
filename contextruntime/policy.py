"""ContextPolicy — the semantic-first, fail-open decision layer (developer preview).

Sits ABOVE the frozen pieces and composes them; it modifies none of them:
  * the observation layer (HookJournal) tells us what KIND of call this is;
  * the SemanticFS read surface (read_symbol / bundle) is the semantic ALTERNATIVE to a raw dump;
  * ``reducers.base.should_reduce`` is the output-side retention rule for non-source dumps;
  * the doctor CapabilityProfile says whether enforcement is even mechanically possible.

Given one tool call, ``decide`` returns a stamped :class:`Decision`. Two invariants are absolute:

  SEMANTIC-FIRST — when the agent is about to raw-read source we can serve semantically (a symbol
  the code-graph knows, whose budgeted bundle is no larger than the raw read), steer it to the
  semantic surface instead. Searches (grep/find) over indexed code likewise prefer handle-returning
  ``context_search`` over raw match dumps.

  FAIL-OPEN — the default is always ``pass_through``. Every branch that is unsure — unknown kind,
  no semantic coverage, an edit-precondition read (C10: never interfere), a capability we have not
  mechanically confirmed, or any raised exception — yields ``pass_through`` with ``fail_open=True``.
  The policy can withhold a recommendation; it can never block or corrupt a tool call by itself.

Default mode is ADVISORY: recommendations are nudges the agent may ignore. ``enforce`` (deny+nudge
at PreToolUse) is gated behind BOTH an explicit mode and a doctor-confirmed capability, so today —
with capabilities still ``?`` — the policy is advisory no matter what mode is requested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

POLICY_VERSION = "context-policy-v1"

# actions, most-intervention last
PASS_THROUGH = "pass_through"          # do nothing; the raw call proceeds unchanged
RECOMMEND_SEMANTIC = "recommend_semantic"  # nudge: use read_symbol/bundle/context_search instead
REDUCE_OUTPUT = "reduce_output"        # defer to the Phase-1 output reducer (non-source dumps)
DENY_NUDGE = "deny_nudge"              # enforce-only: PreToolUse deny + steer (needs capability)

ADVISORY = "advisory"
ENFORCE = "enforce"


@dataclass
class ToolCall:
    """What the policy needs to know about one pending call. All fields default to the safe/unknown
    value so a partially-populated call fails open rather than triggering intervention."""
    kind: str = "unknown"                     # read | edit | search | execution | unknown
    target: Optional[str] = None              # path or symbol id
    token_est: int = 0                        # estimated tokens the raw call would materialize
    is_edit_target: bool = False              # this read is an edit precondition (C10 — never touch)
    is_source: bool = False                   # target is source code the graph can serve
    semantic_available: bool = False          # a symbol/bundle exists for target
    semantic_bundle_tokens: Optional[int] = None  # cost of the semantic alternative (None = unknown)
    reducible_output: bool = False            # a Phase-1 reducer could shrink this raw output


@dataclass
class Decision:
    action: str
    reason: str
    confidence: str                           # high | medium | low
    mode_requested: str
    mode_effective: str                       # advisory even if enforce was asked but uncapable
    enforced: bool
    fail_open: bool
    evidence: dict = field(default_factory=dict)
    policy_version: str = POLICY_VERSION

    @property
    def intervenes(self) -> bool:
        return self.action != PASS_THROUGH


# capabilities that must be CONFIRMED ("yes") before enforcement is mechanically sound
_ENFORCE_CAPS = ("pre_tool_use_hook", "edit_read_tracker_satisfied_by_mcp")


def _can_enforce(capability: Optional[dict]) -> bool:
    caps = (capability or {}).get("capabilities", capability or {})
    return all(caps.get(c) == "yes" for c in _ENFORCE_CAPS)


def _passthrough(reason: str, *, fail_open: bool, mode: str, conf: str = "high",
                 evidence: Optional[dict] = None) -> Decision:
    return Decision(PASS_THROUGH, reason, conf, mode, ADVISORY, False, fail_open, evidence or {})


def decide(call: ToolCall, *, mode: str = ADVISORY, capability: Optional[dict] = None) -> Decision:
    """Pure, total decision. Never raises for a well-formed ToolCall; see :func:`decide_safe`
    for the belt-and-suspenders wrapper used on live events."""
    # C10 — an edit-precondition read is load-bearing context for a mutation; never steer or reduce.
    if call.kind == "edit" or call.is_edit_target:
        return _passthrough("edit_precondition_protected", fail_open=True, mode=mode,
                            evidence={"kind": call.kind, "is_edit_target": call.is_edit_target})

    enforce_ok = mode == ENFORCE and _can_enforce(capability)
    mode_eff = ENFORCE if enforce_ok else ADVISORY

    # SEMANTIC-FIRST — raw source read we can serve from the graph, and the bundle is not larger.
    if call.kind == "read" and call.is_source and call.semantic_available:
        bt = call.semantic_bundle_tokens
        fits = bt is None or call.token_est == 0 or bt <= call.token_est
        if fits:
            action = DENY_NUDGE if enforce_ok else RECOMMEND_SEMANTIC
            ev = {"target": call.target, "raw_tokens": call.token_est, "bundle_tokens": bt,
                  "saved_est": (None if bt is None else max(0, call.token_est - bt))}
            return Decision(action, "source_read_has_semantic_equivalent",
                            "high" if bt is not None else "medium", mode, mode_eff,
                            enforced=enforce_ok, fail_open=False, evidence=ev)
        return _passthrough("semantic_bundle_not_smaller", fail_open=False, mode=mode,
                            conf="medium", evidence={"raw_tokens": call.token_est, "bundle_tokens": bt})

    # SEMANTIC-FIRST for search — handle-returning context_search over indexed code beats raw dumps.
    if call.kind == "search" and call.semantic_available:
        action = DENY_NUDGE if enforce_ok else RECOMMEND_SEMANTIC
        return Decision(action, "search_has_semantic_equivalent", "medium", mode, mode_eff,
                        enforced=enforce_ok, fail_open=False,
                        evidence={"target": call.target, "tool": "context_search"})

    # Non-source reducible dump (logs / test output): defer to the output-side reducer, advisory.
    if call.kind == "read" and call.reducible_output and not call.is_source:
        return Decision(REDUCE_OUTPUT, "non_source_output_reducible", "medium", mode, ADVISORY,
                        enforced=False, fail_open=False,
                        evidence={"target": call.target, "token_est": call.token_est})

    # Everything else — including unknown kind, execution, or source with no semantic coverage.
    return _passthrough("no_semantic_equivalent" if call.kind == "read" else f"kind_{call.kind}_not_steerable",
                        fail_open=call.kind in ("unknown", ""), mode=mode,
                        conf="high", evidence={"kind": call.kind, "semantic_available": call.semantic_available})


def decide_safe(call: ToolCall, *, mode: str = ADVISORY, capability: Optional[dict] = None) -> Decision:
    """Live-event entrypoint: any exception collapses to fail-open pass_through."""
    try:
        return decide(call, mode=mode, capability=capability)
    except Exception as e:  # noqa: BLE001 — the policy must never break a tool call
        return _passthrough(f"policy_error:{type(e).__name__}", fail_open=True, mode=mode,
                            conf="low", evidence={"error": str(e)[:200]})


def advise(decision: Decision) -> Optional[str]:
    """One-line nudge for the developer-preview surface; None when nothing to say (pass_through)."""
    if not decision.intervenes:
        return None
    if decision.action in (RECOMMEND_SEMANTIC, DENY_NUDGE):
        tgt = decision.evidence.get("target") or "this symbol"
        saved = decision.evidence.get("saved_est")
        tail = f" (~{saved} tokens lighter)" if saved else ""
        verb = "Use" if decision.action == RECOMMEND_SEMANTIC else "Blocked raw read — use"
        return f"[contextpolicy] {verb} the semantic read surface for {tgt}{tail}."
    if decision.action == REDUCE_OUTPUT:
        return "[contextpolicy] this output is reducible — the ContextReduce hook would shrink it."
    return None
