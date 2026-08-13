"""Load the per-model pricing table. Dollar figures are ESTIMATES until the table
is verified against current provider pricing (design §4); token figures never are.
"""
from __future__ import annotations

import json
from pathlib import Path

# repo-root pricing.json (sibling of the package dir)
_DEFAULT = Path(__file__).resolve().parent.parent / "pricing.json"


def load_pricing(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _DEFAULT
    if not p.exists():
        return {"models": [], "default": {"input": 5.0, "output": 25.0,
                "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10.0,
                "verified": False}}
    return json.loads(p.read_text())


def price_for(model: str, table: dict) -> tuple[dict, bool]:
    for entry in table.get("models", []):
        if entry.get("match", "\x00") in (model or ""):
            return entry, bool(entry.get("verified", False))
    d = table.get("default", {})
    return d, bool(d.get("verified", False))
