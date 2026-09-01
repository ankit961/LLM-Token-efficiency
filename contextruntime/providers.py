"""Provider profiles — the constants that make the framework generic.

Every algorithm in this runtime reduces to provider-specific constants:

  read_mult    price of a cached-prefix read, × base input
  write_mult   price of writing the cache (the mutation-invalidation penalty), × base input
  ttl_s        cache lifetime after last touch
  out_mult     output price, × base input

and one derived number that governs the whole economics of history mutation:

  break_even_reads = (write_mult − read_mult) / read_mult
      = how many future cached reads one rewritten token must earn back
        before retiring it mid-session is profitable.

Anthropic 1h tier: 19 (mutation rarely pays mid-session — hold, fire at cold moments).
OpenAI-style free cache writes: 1 (mutation almost always pays).
The SAME scheduler inequality produces opposite live behavior from these constants alone —
which is the point: the framework is generic; the constants are not.

VALIDATION STATUS MATTERS. Only `anthropic-1h` is live-validated (calibrated exact on native
sessions; 0.2pp on a preregistered live prediction — B7/B8). Every other profile is a modeling
preset built from public pricing structure: verify ratios against current provider pricing
before quoting, and treat cache SEMANTICS (interior hits, TTL softness, breakpoints) as unknown
until a per-provider calibration is run — Anthropic's own semantics had to be discovered
empirically, and other providers will have their own surprises.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    read_mult: float
    write_mult: float
    ttl_s: float
    out_mult: float
    validated: bool                # live-calibrated in THIS repo (B7/B8) — not a pricing claim
    note: str = ""

    @property
    def break_even_reads(self) -> float:
        """Future cached reads one rewritten token must earn back for mutation to pay."""
        return (self.write_mult - self.read_mult) / self.read_mult


PROFILES = {
    "anthropic-1h": ProviderProfile(
        "anthropic-1h", read_mult=0.1, write_mult=2.0, ttl_s=3600.0, out_mult=5.0,
        validated=True,
        note="The live-validated profile: captured cache_control ttl='1h'; writes 2.0x; "
             "sonnet output ratio $15/$3. Calibration: exact on 11/12 native sessions, "
             "0.2pp on the preregistered B8 live prediction."),
    "anthropic-5m": ProviderProfile(
        "anthropic-5m", read_mult=0.1, write_mult=1.25, ttl_s=300.0, out_mult=5.0,
        validated=False,
        note="Anthropic's default 5-minute tier (writes 1.25x). Same semantics as the "
             "validated profile, different constants; break-even 11.5 instead of 19."),
    "openai-auto": ProviderProfile(
        "openai-auto", read_mult=0.5, write_mult=1.0, ttl_s=600.0, out_mult=4.0,
        validated=False,
        note="OpenAI-style automatic prefix caching: cached input ~50% off, NO write premium "
             "(write_mult=1.0 means invalidated suffix simply bills as normal input), TTL "
             "minutes-scale. Break-even 1 — mutation almost always pays. Ratios are a modeling "
             "preset; verify against current pricing, then calibrate semantics before claiming."),
    "gemini-implicit": ProviderProfile(
        "gemini-implicit", read_mult=0.25, write_mult=1.0, ttl_s=300.0, out_mult=4.0,
        validated=False,
        note="Gemini-style implicit caching: cached-hit discount ~75%, no write premium. "
             "Gemini's EXPLICIT cache is storage-priced per token-hour — a different objective "
             "this prefix model does not represent (residency there has a direct $/hour meter, "
             "which favors retirement even more). Modeling preset; verify then calibrate."),
}


def profile_from_env(default: str = "anthropic-1h") -> ProviderProfile:
    """CR_GATEWAY_PROFILE selects the provider constants for the scheduler's break-even rule.
    Unknown names fall back to the validated default (safe: the strictest break-even)."""
    return PROFILES.get((os.environ.get("CR_GATEWAY_PROFILE") or default).strip().lower(),
                        PROFILES[default])
