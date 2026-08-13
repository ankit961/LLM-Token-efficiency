"""Pick a language adapter by file extension, preferring the highest-fidelity one
that is actually available in this environment."""
from __future__ import annotations

from .adapters import (Adapter, HeuristicAdapter, PythonAstAdapter,
                       TreeSitterAdapter, TreeSitterUnavailable)

# extension -> language
_EXT = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java",
}

# languages we can parse structurally without tree-sitter
_HEURISTIC_OK = {"javascript", "typescript"}

_cache: dict[str, Adapter | None] = {}


def _build(language: str) -> Adapter | None:
    if language == "python":
        return PythonAstAdapter()
    # prefer tree-sitter when its grammar is importable
    try:
        return TreeSitterAdapter(language)
    except (TreeSitterUnavailable, KeyError):
        pass
    if language in _HEURISTIC_OK:
        return HeuristicAdapter(language)
    return None


def get_adapter(path: str) -> Adapter | None:
    ext = path[path.rfind("."):] if "." in path else ""
    language = _EXT.get(ext)
    if not language:
        return None
    if language not in _cache:
        _cache[language] = _build(language)
    return _cache[language]


def available_adapters() -> dict[str, str]:
    """language -> parser actually in use (for the Doctor / reports)."""
    out = {}
    for language in sorted(set(_EXT.values())):
        a = _cache.get(language) or _build(language)
        _cache[language] = a
        out[language] = a.parser if a else "none"
    return out
