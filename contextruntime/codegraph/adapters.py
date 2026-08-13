"""Language adapters: source -> (symbols, edges) with confidence + resolution.

Adapters produce intermediate ParsedSymbol/ParsedEdge using a caller-supplied
package-qualified module name (module identity is decided by the builder, which
knows the repo layout — a filename alone is ambiguous). The builder then resolves
each edge's target to a symbol_id with an explicit match_kind (see builder.resolve).

Confidence is per (adapter, edge_type): structural containment is near-certain;
call/reference resolution is where language dynamism bites, so it scores lower —
and lower still for dynamic languages parsed heuristically.
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
    src_qname: str          # qualified name of the source symbol ("" = module scope)
    dst_name: str           # target name (resolved to a symbol_id by the builder)
    edge_type: str
    confidence: float       # base confidence before resolution discounting
    resolution: str         # provenance: python_ast | tree_sitter | regex_heuristic


class Adapter:
    language = "?"
    parser = "?"
    resolution_quality = 0.5

    def parse(self, rel_path: str, source: str, module_q: str
              ) -> tuple[list[ParsedSymbol], list[ParsedEdge]]:
        raise NotImplementedError


# --------------------------------------------------------------------- Python

class PythonAstAdapter(Adapter):
    """Exact Python structure via stdlib ast — no external dependency."""
    language = "python"
    parser = "python_ast"
    resolution_quality = 0.95

    def parse(self, rel_path, source, module_q):
        syms: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return syms, edges
        is_test_file = "test" in rel_path.rsplit("/", 1)[-1].lower()
        syms.append(ParsedSymbol(module_q, "module", rel_path, "python",
                                 1, _last_line(tree), "", content_hash(source)))

        def add_callable(node, qn, kind):
            seg = ast.get_source_segment(source, node) or ""
            sig = f"{node.name}({ast.unparse(node.args)})"
            syms.append(ParsedSymbol(qn, kind, rel_path, "python", node.lineno,
                        getattr(node, "end_lineno", node.lineno), sig, content_hash(seg)))
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
                add_callable(node, qn, kind)
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 1.0, "python_ast"))
                if kind == "test":
                    _link_test(node.name, edges, qn)
            elif isinstance(node, ast.ClassDef):
                qn = f"{module_q}.{node.name}"
                seg = ast.get_source_segment(source, node) or ""
                syms.append(ParsedSymbol(qn, "class", rel_path, "python", node.lineno,
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
                        add_callable(m, mqn, kind)
                        edges.append(ParsedEdge(qn, mqn, "CONTAINS", 1.0, "python_ast"))
        return syms, edges


def _callee(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _link_test(test_name: str, edges: list, test_qn: str) -> None:
    target = test_name[5:] if test_name.startswith("test_") else test_name
    if target:
        edges.append(ParsedEdge(test_qn, target, "TESTED_BY", 0.5, "regex_heuristic"))


def _last_line(tree) -> int:
    return max((getattr(n, "end_lineno", 0) or 0) for n in ast.walk(tree)) or 1


# ------------------------------------------------------------------ Heuristic

_RE = {
    "func": re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.M),
    "arrow": re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", re.M),
    "class": re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([A-Za-z_$][\w$.]*))?", re.M),
    "import": re.compile(r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""", re.M),
}


class HeuristicAdapter(Adapter):
    """Regex fallback for languages without a better adapter. Low confidence —
    exactly the dynamic-language signal the design wants surfaced (C3). It parses
    structure only; it deliberately does not emit CALLS (attribution would be a guess)."""
    parser = "regex_heuristic"
    resolution_quality = 0.6

    def __init__(self, language="javascript"):
        self.language = language

    def parse(self, rel_path, source, module_q):
        syms, edges = [], []
        syms.append(ParsedSymbol(module_q, "module", rel_path, self.language, 1,
                                 source.count("\n") + 1, "", content_hash(source)))
        def line_of(pos): return source.count("\n", 0, pos) + 1
        for m in _RE["import"].finditer(source):
            edges.append(ParsedEdge(module_q, m.group(1) or m.group(2), "IMPORTS", 0.7, "regex_heuristic"))
        for key, kind in (("func", "function"), ("arrow", "function"), ("class", "class")):
            for m in _RE[key].finditer(source):
                qn = f"{module_q}.{m.group(1)}"
                syms.append(ParsedSymbol(qn, kind, rel_path, self.language, line_of(m.start()),
                            line_of(m.start()), m.group(1), content_hash(m.group(0))))
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 0.7, "regex_heuristic"))
                if key == "class" and m.lastindex and m.group(2):
                    edges.append(ParsedEdge(qn, m.group(2), "IMPLEMENTS", 0.55, "regex_heuristic"))
        return syms, edges


# ---------------------------------------------------------------- tree-sitter

class TreeSitterUnavailable(RuntimeError):
    pass


class TreeSitterAdapter(Adapter):
    """Real tree-sitter parsing (used when the grammar imports cleanly). Tracks
    enclosing scope so methods are module.Class.method, and recurses the full
    subtree for calls so nested calls are not missed."""
    parser = "tree_sitter"
    resolution_quality = 0.9

    _GRAMMAR = {"javascript": "tree_sitter_javascript",
                "typescript": "tree_sitter_typescript",
                "go": "tree_sitter_go", "rust": "tree_sitter_rust",
                "java": "tree_sitter_java"}
    _CLASS = {"class_declaration", "class"}
    _FUNC = {"function_declaration", "function", "method_definition",
             "method_declaration", "function_definition"}

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

    def parse(self, rel_path, source, module_q):
        syms, edges = [], []
        src = source.encode("utf-8", "replace")
        tree = self._parser.parse(src)
        syms.append(ParsedSymbol(module_q, "module", rel_path, self.language, 1,
                                 source.count("\n") + 1, "", content_hash(source)))

        def txt(node):
            return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

        def name_of(node):
            n = node.child_by_field_name("name")
            return txt(n) if n else None

        def emit_calls(node, func_q):
            # recurse the WHOLE subtree of a definition for calls (not just direct children)
            for c in _descendants(node):
                if c.type == "call_expression":
                    fn = c.child_by_field_name("function")
                    if fn:
                        edges.append(ParsedEdge(func_q, txt(fn).split(".")[-1],
                                                "CALLS", 0.7, "tree_sitter"))

        def walk(node, type_scope, container_q):
            for child in node.children:
                if child.type == "import_statement":
                    s = child.child_by_field_name("source")
                    if s:
                        edges.append(ParsedEdge(module_q, txt(s).strip("'\""),
                                                "IMPORTS", 0.85, "tree_sitter"))
                    walk(child, type_scope, container_q)
                elif child.type in self._CLASS:
                    nm = name_of(child)
                    if not nm:
                        walk(child, type_scope, container_q); continue
                    qn = f"{container_q}.{nm}"
                    syms.append(ParsedSymbol(qn, "class", rel_path, self.language,
                                child.start_point[0] + 1, child.end_point[0] + 1, nm,
                                content_hash(txt(child))))
                    edges.append(ParsedEdge(container_q, qn, "CONTAINS", 0.9, "tree_sitter"))
                    sc = child.child_by_field_name("superclass") or child.child_by_field_name("superclasses")
                    if sc:
                        edges.append(ParsedEdge(qn, txt(sc).split()[-1].split(".")[-1],
                                                "IMPLEMENTS", 0.7, "tree_sitter"))
                    walk(child, qn, qn)                 # class becomes the container scope
                elif child.type in self._FUNC:
                    nm = name_of(child)
                    if not nm:
                        walk(child, type_scope, container_q); continue
                    kind = "method" if type_scope else "function"
                    qn = f"{container_q}.{nm}"
                    syms.append(ParsedSymbol(qn, kind, rel_path, self.language,
                                child.start_point[0] + 1, child.end_point[0] + 1, nm,
                                content_hash(txt(child))))
                    edges.append(ParsedEdge(container_q, qn, "CONTAINS", 0.9, "tree_sitter"))
                    emit_calls(child, qn)               # attribute nested calls to this func
                else:
                    walk(child, type_scope, container_q)

        walk(tree.root_node, None, module_q)
        return syms, edges


def _descendants(node):
    stack = list(node.children)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)
