"""ContextReduce (design v1.2 §7) — Phase 1.

PostToolUse output reduction: replace a fat tool result the model sees with a
decision-relevant summary plus a handle to the full raw payload, WITHOUT proxying
the model request. The cheapest safe win — it ships on a plain subscription.

Honest posture (design §7, implementation chapter):
  - version-gated: built-in-tool output replacement is a recent, client-dependent
    capability; the hook degrades LOUDLY (no-op) if the ContextRuntime Doctor
    cannot confirm it, rather than silently doing nothing.
  - synchronous & schema-perfect: a reducer must preserve the tool_response shape;
    a malformed replacement aborts the turn.
  - retention (C10): never reduce a read that is a likely edit precondition.
  - fail-open: any error returns {} + exit 0 so the agent never freezes.
"""
from .base import ReducedOutput, should_reduce, make_handle          # noqa: F401
from .library import reduce_result, classify, REGISTRY               # noqa: F401
