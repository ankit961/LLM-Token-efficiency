"""Language adapters: source -> (symbols, edges) with confidence + resolution.

Each adapter returns intermediate ParsedSymbol/ParsedEdge; the builder resolves
edge targets to symbol_ids and persists. Confidence is per (adapter, edge_type):
structural containment is near-certain; call/reference resolution is where language
dynamism bites, so it is scored lower and lower still for dynamic languages.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..model import content_hash


@dataclass
class ParsedSymbol:
    qualified_name: str
    kind: str
    path: str
    language: str
    start_line: int
    end_line: int
    signature: str
    content_hash: str


@dataclass
class ParsedEdge:
    src_qname: str          # qualified name of the source symbol ("" for module-level)
    dst_name: str           # target name (resolved to a symbol_id later, if possible)
    edge_type: str
    confidence: float
    resolution: str


class Adapter:
    language = "?"
    parser = "?"
    resolution_quality = 0.5

    def parse(self, path: str, source: str) -> tuple[list[ParsedSymbol], list[ParsedEdge]]:
        raise NotImplementedError


# --------------------------------------------------------------------- Python

class PythonAstAdapter(Adapter):
    """Exact Python structure via stdlib ast — no external dependency."""
    language = "python"
    parser = "python_ast"
    resolution_quality = 0.95

    def parse(self, path, source):
        syms: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return syms, edges
        module_q = _module_name(path)
        is_test_file = "test" in path.rsplit("/", 1)[-1].lower()
        syms.append(ParsedSymbol(module_q, "module", path, "python",
                                 1, _last_line(tree), "", content_hash(source)))

        def add_def(node, qn, kind):
            seg = ast.get_source_segment(source, node) or ""
            sig = f"{node.name}({ast.unparse(node.args)})" if hasattr(node, "args") else node.name
            syms.append(ParsedSymbol(qn, kind, path, "python",
                                     node.lineno, getattr(node, "end_lineno", node.lineno),
                                     sig, content_hash(seg)))
            # calls inside the body -> CALLS
            for c in ast.walk(node):
                if isinstance(c, ast.Call):
                    name = _callee(c.func)
                    if name:
                        edges.append(ParsedEdge(qn, name, "CALLS", 0.75, "python_ast"))

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None)
                for a in node.names:
                    tgt = f"{mod}.{a.name}" if mod else a.name
                    edges.append(ParsedEdge(module_q, tgt, "IMPORTS", 0.95, "python_ast"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = f"{module_q}.{node.name}"
                kind = "test" if (is_test_file or node.name.startswith("test_")) else "function"
                add_def(node, qn, kind)
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 1.0, "python_ast"))
                if kind == "test":
                    _link_test(node.name, edges, qn)
            elif isinstance(node, ast.ClassDef):
                qn = f"{module_q}.{node.name}"
                seg = ast.get_source_segment(source, node) or ""
                syms.append(ParsedSymbol(qn, "class", path, "python", node.lineno,
                            getattr(node, "end_lineno", node.lineno),
                            f"class {node.name}", content_hash(seg)))
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 1.0, "python_ast"))
                for base in node.bases:
                    bn = _callee(base)
                    if bn:
                        edges.append(ParsedEdge(qn, bn, "IMPLEMENTS", 0.85, "python_ast"))
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mqn = f"{qn}.{m.name}"
                        kind = "test" if m.name.startswith("test_") else "method"
                        add_def(m, mqn, kind)
                        edges.append(ParsedEdge(qn, mqn, "CONTAINS", 1.0, "python_ast"))
        return syms, edges


def _callee(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _link_test(test_name: str, edges: list, test_qn: str) -> None:
    # heuristic: test_foo -> a symbol named foo (low confidence)
    target = test_name[5:] if test_name.startswith("test_") else test_name
    if target:
        edges.append(ParsedEdge(test_qn, target, "TESTED_BY", 0.5, "regex_heuristic"))


def _module_name(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _last_line(tree) -> int:
    return max((getattr(n, "end_lineno", 0) or 0) for n in ast.walk(tree)) or 1


# ------------------------------------------------------------------ Heuristic

_RE = {
    "func": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.M),
    "arrow": re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", re.M),
    "class": re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([A-Za-z_$][\w$.]*))?", re.M),
    "import": re.compile(r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""", re.M),
    "call": re.compile(r"\b([A-Za-z_$][\w$]*)\s*\("),
}


class HeuristicAdapter(Adapter):
    """Regex fallback for languages without a better adapter. Low confidence —
    exactly the dynamic-language signal the design wants surfaced (C3)."""
    parser = "regex_heuristic"
    resolution_quality = 0.6

    def __init__(self, language="javascript"):
        self.language = language

    def parse(self, path, source):
        syms, edges = [], []
        module_q = _module_name(path)
        syms.append(ParsedSymbol(module_q, "module", path, self.language, 1,
                                 source.count("\n") + 1, "", content_hash(source)))
        def line_of(pos): return source.count("\n", 0, pos) + 1
        for m in _RE["import"].finditer(source):
            tgt = m.group(1) or m.group(2)
            edges.append(ParsedEdge(module_q, tgt, "IMPORTS", 0.7, "regex_heuristic"))
        for key, kind in (("func", "function"), ("arrow", "function"), ("class", "class")):
            for m in _RE[key].finditer(source):
                name = m.group(1)
                qn = f"{module_q}.{name}"
                syms.append(ParsedSymbol(qn, kind, path, self.language, line_of(m.start()),
                            line_of(m.start()), name, content_hash(m.group(0))))
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 0.7, "regex_heuristic"))
                if key == "class" and m.lastindex and m.group(2):
                    edges.append(ParsedEdge(qn, m.group(2), "IMPLEMENTS", 0.55, "regex_heuristic"))
        # module-level CALLS (can't attribute to a function reliably -> low confidence)
        for m in _RE["call"].finditer(source):
            edges.append(ParsedEdge(module_q, m.group(1), "CALLS", 0.5, "regex_heuristic"))
        return syms, edges


# ---------------------------------------------------------------- tree-sitter

class TreeSitterUnavailable(RuntimeError):
    pass


class TreeSitterAdapter(Adapter):
    """Real tree-sitter parsing, used when the grammar for `language` imports
    cleanly. Higher fidelity than regex for dynamic languages. Activated by the
    registry only when available; otherwise the registry uses HeuristicAdapter."""
    parser = "tree_sitter"
    resolution_quality = 0.9

    _GRAMMAR = {"javascript": "tree_sitter_javascript",
                "typescript": "tree_sitter_typescript",
                "go": "tree_sitter_go", "rust": "tree_sitter_rust",
                "java": "tree_sitter_java"}
    _DEFN = {"function_declaration", "method_definition", "class_declaration",
             "function_definition", "method_declaration", "type_declaration"}

    def __init__(self, language: str):
        self.language = language
        try:
            import tree_sitter  # noqa: F401
            mod = __import__(self._GRAMMAR[language])
            from tree_sitter import Language, Parser
            self._lang = Language(mod.language())
            self._parser = Parser(self._lang)
        except Exception as e:  # noqa: BLE001
            raise TreeSitterUnavailable(str(e))

    def parse(self, path, source):
        syms, edges = [], []
        module_q = _module_name(path)
        src = source.encode("utf-8", "replace")
        tree = self._parser.parse(src)
        syms.append(ParsedSymbol(module_q, "module", path, self.language, 1,
                                 source.count("\n") + 1, "", content_hash(source)))

        def name_of(node):
            n = node.child_by_field_name("name")
            return src[n.start_byte:n.end_byte].decode("utf-8", "replace") if n else None

        def walk(node):
            if node.type in self._DEFN:
                nm = name_of(node)
                if nm:
                    qn = f"{module_q}.{nm}"
                    kind = "class" if "class" in node.type else \
                           ("method" if "method" in node.type else "function")
                    seg = src[node.start_byte:node.end_byte].decode("utf-8", "replace")
                    syms.append(ParsedSymbol(qn, kind, path, self.language,
                                node.start_point[0] + 1, node.end_point[0] + 1,
                                nm, content_hash(seg)))
                    edges.append(ParsedEdge(module_q, qn, "CONTAINS", 0.9, "tree_sitter"))
                    for c in node.children:
                        if c.type in ("call_expression",):
                            fn = c.child_by_field_name("function")
                            if fn:
                                edges.append(ParsedEdge(qn,
                                    src[fn.start_byte:fn.end_byte].decode("utf-8", "replace").split(".")[-1],
                                    "CALLS", 0.7, "tree_sitter"))
            for c in node.children:
                walk(c)

        walk(tree.root_node)
        return syms, edges
