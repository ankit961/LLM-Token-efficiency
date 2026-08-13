"""ContextRuntime Doctor — runtime capability probe (design C11).

Emits a CapabilityProfile + evidence grade that is stamped on every ledger/benchmark
report, so numbers from a full-reduction client are not compared with numbers from
one without it.

Phase 0b status: this is a STRUCTURED STUB. The capability values below are the
design's working assumptions (Appendix B), each marked "?" = verify-at-runtime.
Live probing (does PostToolUse `updatedToolOutput` apply? does the MCP path carry a
session id? what is the effective cache window?) requires the hook/MCP adapters,
which land in Phase 2 — so nothing here is asserted as confirmed.
"""
from __future__ import annotations

import shutil

from .model import CapabilityProfile

# name -> (assumed_state, note). "?" means unverified until the adapters exist.
_ASSUMPTIONS = {
    "pre_tool_use_hook": ("?", "allow/deny/ask only; cannot rewrite/substitute a tool"),
    "post_tool_use_output_replacement": ("?", "updatedToolOutput — version-gated"),
    "mcp_output_replacement": ("?", "updatedMCPToolOutput"),
    "raw_payload_visibility": ("?", "large outputs may be pre-truncated to a file ref"),
    "tool_identity_rewrite": ("no", "not supported — reduction is output-side only"),
    "edit_read_tracker_satisfied_by_mcp": ("?", "may force native edit-precondition reads"),
    "bash_interception": ("best_effort", "shell classifier narrows, does not close"),
    "hook_session_id": ("?", "present on hook stdin"),
    "mcp_session_id": ("no", "absent — correlate via pid-file"),
    "effective_cache_window": ("?", "measured per account/model, never assumed"),
    "otel_exporter": ("?", "opt-in; JSONL is the load-bearing source"),
    "statusline_rate_limits": ("?", "only if the daemon is the statusLine command"),
}


def probe(client: str = "claude-code", client_version: str | None = None) -> CapabilityProfile:
    caps = {name: state for name, (state, _note) in _ASSUMPTIONS.items()}
    detected_cli = shutil.which("claude") is not None
    # Grade is C until live probing confirms capabilities (design §4.4).
    reduction = "unknown"
    admission = "unknown"
    return CapabilityProfile(
        client=client,
        client_version=client_version,
        capabilities=caps,
        reduction_mode=reduction,
        admission_mode=admission,
        evidence_grade="C",
    )


def stamp(profile: CapabilityProfile) -> str:
    """One-line stamp for report headers."""
    return (f"{profile.client}{'@'+profile.client_version if profile.client_version else ''} "
            f"reduction={profile.reduction_mode} admission={profile.admission_mode} "
            f"grade={profile.evidence_grade}")


def format_report(profile: CapabilityProfile) -> str:
    lines = ["ContextRuntime Doctor — capability probe (Phase 0b stub)",
             f"  client: {profile.client}  grade: {profile.evidence_grade}",
             "  (values are design assumptions; '?' = verify-at-runtime once adapters land)",
             ""]
    for name, (state, note) in _ASSUMPTIONS.items():
        mark = {"yes": "✓", "no": "✗", "?": "?", "best_effort": "~"}.get(state, "?")
        lines.append(f"  {mark} {name:38s} {state:12s} {note}")
    lines += ["",
              f"  reduction mode : {profile.reduction_mode}",
              f"  admission mode : {profile.admission_mode}"]
    return "\n".join(lines)
