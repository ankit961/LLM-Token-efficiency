"""Best-effort secret redaction applied before any raw payload is persisted.

The residency graph's CAS stores bounded tool/source payloads (design §9) so handles
resolve. Those payloads can contain secrets, so we scrub high-signal patterns before
storage and before any reduced text is emitted. This is BEST-EFFORT — it will not
catch every secret; deployments handling regulated data should also constrain what
tools may read. (Design §10: redact before materialization.)
"""
from __future__ import annotations

import re

# (label, pattern, group-to-redact) — group 0 means the whole match.
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("aws-akid",   re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    ("gh-token",   re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), 0),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), 0),
    ("slack",      re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("jwt",        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), 0),
    ("bearer",     re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"), 0),
    ("pem",        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), 0),
    # KEY=VALUE / KEY: VALUE where the key name looks secret -> redact the value
    ("env-secret", re.compile(
        r"(?im)^([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|"
        r"PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)[A-Z0-9_]*)\s*[=:]\s*(\S+)"), 2),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for label, pat, grp in _PATTERNS:
        if grp == 0:
            out = pat.sub(f"[REDACTED:{label}]", out)
        else:
            def _sub(m, _lbl=label, _g=grp):
                s = m.group(0)
                return s.replace(m.group(_g), f"[REDACTED:{_lbl}]")
            out = pat.sub(_sub, out)
    return out


def redaction_count(text: str) -> int:
    return sum(len(p.findall(text)) for _l, p, _g in _PATTERNS)
