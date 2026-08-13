"""CodeSymbol graph — Graph-Lite (design v1.2 §8/§9, C1). Phase 2.

Language adapters parse source into symbols + edges; every edge carries a
confidence + resolution provenance so dependency bundles adapt to how sound the
analysis is per language (C3). Adapters, in descending fidelity:

  python_ast      stdlib `ast`      — Python, exact structure, no dependency
  tree_sitter     tree-sitter       — JS/TS/Go/… when the grammar is installed
  regex_heuristic regexes           — fallback for anything else (low confidence)

Deferred (later Phase 2): budgeted DEPENDS_ON bundle generation, read
classification, and the MCP read surface (read_symbol/read_slice/find_callers).
"""
from .builder import index_path, IndexReport            # noqa: F401
from .registry import get_adapter, available_adapters   # noqa: F401
