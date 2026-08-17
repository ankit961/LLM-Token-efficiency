"""ContextRuntime Doctor — runtime capability probe (design C11).

Emits a CapabilityProfile + evidence grade that is stamped on every ledger/benchmark
report, so numbers from a full-reduction client are not compared with numbers from
one without it.

Phase 0b status: mostly a STRUCTURED STUB -- the capability values below are the design's
working assumptions (Appendix B), each marked "?" = unverified until live-tested. Two have
since been confirmed by live/primary-source verification (`pre_tool_use_hook`,
`post_tool_use_output_replacement` -- see their notes for method + scope) and are stamped
"yes"; the rest remain "?" until probed the same way. `evidence_grade` stays C until enough
of the table is confirmed to warrant moving it, not just the two verified so far.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .model import CapabilityProfile

_SEMVER = re.compile(r"\d+\.\d+\.\d+")
LIVE_VERSION_TTL = 300          # seconds a cached `claude --version` probe stays fresh


def _parse_semver(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _SEMVER.search(raw)
    return m.group(0) if m else None


def _version_cache_path() -> str:
    return str(Path(os.environ.get("CR_VERSION_CACHE")
                    or (Path.home() / ".contextruntime" / "client_version.json")))


def live_client_version(*, ttl: int = LIVE_VERSION_TTL, now: float | None = None) -> str | None:
    """The ACTUAL running client version, so the allowlist gate is runtime — not the value baked
    into the hook command at install time (which a client auto-update would silently outdate).
    A short-TTL file cache keeps this to ~one `claude --version` per window during a burst of
    reductions. `CR_LIVE_CLIENT_VERSION` short-circuits the probe (deterministic tests/CI). Returns
    None (⇒ caller falls back to the baked value) on any failure; never raises."""
    override = os.environ.get("CR_LIVE_CLIENT_VERSION")
    if override is not None:
        return _parse_semver(override)
    now = time.time() if now is None else now
    path = _version_cache_path()
    try:
        with open(path) as f:
            cached = json.load(f)
        if now - cached.get("ts", 0) < ttl:
            return cached.get("version")
    except Exception:                       # noqa: BLE001 — no/at stale cache: re-probe
        pass
    version = None
    try:
        cli = shutil.which("claude")
        if cli:
            out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=10)
            version = _parse_semver((out.stdout or out.stderr).strip())
    except Exception:                       # noqa: BLE001 — probe failure ⇒ None (fall back to baked)
        version = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": version, "ts": now}, f)
    except Exception:                       # noqa: BLE001 — caching is best-effort
        pass
    return version

# Client versions on which post_tool_use_output_replacement has been LIVE-VERIFIED (see the
# capability note below). Transparent-reduction ENFORCEMENT is permitted ONLY on a version in
# this set; every other version fails safe to pass-through (Transparent Reduction Contract v0.1
# §4.4, and the user-chosen "does nothing when uncertain" posture). Extend this set only after a
# fresh live verification on the new version — never speculatively.
CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS = frozenset({"2.1.229"})


def output_replacement_confirmed(client_version: str | None) -> bool:
    """True only for a client version where output replacement was live-verified. An unknown or
    missing version is NOT confirmed — the caller must pass the tool output through unchanged."""
    return bool(client_version) and client_version in CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS

# name -> (assumed_state, note). "?" means unverified until the adapters exist.
_ASSUMPTIONS = {
    # verified against Claude Code's hook docs (primary source, agent-checked): PreToolUse stdout
    # is NOT shown to the model -- allow/deny/escalate only, cannot inject visible content or
    # rewrite/substitute a tool call. Confirmed contract, not a live-client probe.
    "pre_tool_use_hook": ("yes", "confirmed allow/deny/escalate only; cannot rewrite/substitute "
                                  "a tool or inject model-visible content (PreToolUse stdout is "
                                  "not shown to the model)"),
    # LIVE-VERIFIED on Claude Code 2.1.229: a controlled test (grep returning 200 real matching
    # lines; PostToolUse wired to contextruntime.reducers.hook with CR_OUTPUT_REPLACEMENT=1 +
    # CR_REDUCE_MODE=enforce) showed the model receiving 15 lines + a result:// handle -- the
    # reducer's OWN handle scheme, not any native Claude Code truncation. A no-hook control on the
    # identical prompt/data showed the full 200 raw lines, no handle. This is real output
    # replacement working end to end, not a documentation claim. Version-gated: re-verify on other
    # client versions before relying on it there.
    "post_tool_use_output_replacement": ("yes", "updatedToolOutput confirmed live on Claude Code "
                                                "2.1.229 -- grep output genuinely replaced with a "
                                                "result:// handle, verified against a no-hook "
                                                "control; version-gated for other clients"),
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
             "  ('yes'/'no' = verified; '?' = unverified design assumption, not yet live-tested)",
             ""]
    for name, (state, note) in _ASSUMPTIONS.items():
        mark = {"yes": "✓", "no": "✗", "?": "?", "best_effort": "~"}.get(state, "?")
        lines.append(f"  {mark} {name:38s} {state:12s} {note}")
    lines += ["",
              f"  reduction mode : {profile.reduction_mode}",
              f"  admission mode : {profile.admission_mode}"]
    return "\n".join(lines)
