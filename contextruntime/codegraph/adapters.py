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
    source: str = ""          # the symbol's source segment (stored redacted, for the materializer)


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
                        getattr(node, "end_lineno", node.lineno), sig, content_hash(seg),
                        source=seg))
            # Calls in THIS function's own lexical scope only — a call inside a nested
            # def belongs to that def, not to this one (parity with the tree-sitter walk).
            for c in _own_scope_calls(node):
                name = _callee(c.func)
                if name:
                    edges.append(ParsedEdge(qn, name, "CALLS", 0.75, "python_ast"))
            # Nested definitions become their own scope-correct symbols.
            _emit_nested(node, qn)

        def emit_class(node, qn):
            seg = ast.get_source_segment(source, node) or ""
            syms.append(ParsedSymbol(qn, "class", rel_path, "python", node.lineno,
                        getattr(node, "end_lineno", node.lineno),
                        f"class {node.name}", content_hash(seg), source=seg))
            for base in node.bases:
                bn = _callee(base)
                if bn:
                    edges.append(ParsedEdge(qn, bn, "IMPLEMENTS", 0.85, "python_ast"))
            # descend through class-body control flow — a method under `if TYPE_CHECKING:`
            # or `try:` is still a method.
            for m in _nested_defs(node):
                if isinstance(m, ast.ClassDef):
                    cqn = f"{qn}.{m.name}"
                    emit_class(m, cqn)
                    edges.append(ParsedEdge(qn, cqn, "CONTAINS", 1.0, "python_ast"))
                else:                                    # (Async)FunctionDef
                    mqn = f"{qn}.{m.name}"
                    kind = "test" if m.name.startswith("test_") else "method"
                    add_callable(m, mqn, kind)
                    edges.append(ParsedEdge(qn, mqn, "CONTAINS", 1.0, "python_ast"))

        def _emit_nested(scope_node, parent_qn):
            for child in _nested_defs(scope_node):
                if isinstance(child, ast.ClassDef):
                    cqn = f"{parent_qn}.{child.name}"
                    emit_class(child, cqn)
                    edges.append(ParsedEdge(parent_qn, cqn, "CONTAINS", 1.0, "python_ast"))
                else:                                    # (Async)FunctionDef
                    cqn = f"{parent_qn}.{child.name}"
                    add_callable(child, cqn, "function")
                    edges.append(ParsedEdge(parent_qn, cqn, "CONTAINS", 1.0, "python_ast"))

        # Imports: module-level (top of tree.body), as before.
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None)
                for a in node.names:
                    tgt = f"{mod}.{a.name}" if mod else a.name
                    edges.append(ParsedEdge(module_q, tgt, "IMPORTS", 0.95, "python_ast"))
        # Definitions: descend through module-level control flow so a def/class inside a
        # `try:`/`if:` block is emitted (not just direct children of the module body).
        for node in _nested_defs(tree):
            if isinstance(node, ast.ClassDef):
                qn = f"{module_q}.{node.name}"
                emit_class(node, qn)
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 1.0, "python_ast"))
            else:                                        # (Async)FunctionDef
                qn = f"{module_q}.{node.name}"
                kind = "test" if (is_test_file or node.name.startswith("test_")) else "function"
                add_callable(node, qn, kind)
                edges.append(ParsedEdge(module_q, qn, "CONTAINS", 1.0, "python_ast"))
                if kind == "test":
                    _link_test(node.name, edges, qn)
        return syms, edges


def _by_pos(nodes: list) -> list:
    """Deterministic source order — same symbols/edges regardless of traversal order."""
    return sorted(nodes, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def _own_scope_calls(scope_node) -> list:
    """Call nodes the function actually *runs* — lexically inside its BODY, but NOT
    inside a nested def/class (those have their own scope). Descends through control-flow
    and call arguments, so ``f(g())`` yields both f and g, but ``def inner(): h()`` inside
    does not yield h.

    Seeds from ``scope_node.body`` only — deliberately NOT from ``ast.iter_child_nodes``,
    which for a FunctionDef also yields the decorator_list, argument default expressions,
    and parameter/return annotation nodes. Those execute at DEFINITION time in the
    *enclosing* scope (or not at all, under ``from __future__ import annotations``), so
    their calls are not calls this function makes and must not be attributed to it. E.g.
    for ``@reg(factory())`` / ``def f(x=default()) -> Ret(): work()`` only ``work`` is f's."""
    out, stack = [], list(getattr(scope_node, "body", []))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                     # separate scope — skip its subtree
        if isinstance(n, ast.Call):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return _by_pos(out)


def _nested_defs(scope_node) -> list:
    """Top-most def/class definitions nested anywhere inside `scope_node` (including
    within if/for/with/try blocks), without descending into a def/class once found.
    Used at module, class-body, and function scope so a definition hidden inside a
    control-flow block is never dropped."""
    out, stack = [], list(ast.iter_child_nodes(scope_node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(n)                                # yield; do not descend into it
        else:
            stack.extend(ast.iter_child_nodes(n))
    return _by_pos(out)


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
                            line_of(m.start()), m.group(1), content_hash(m.group(0)),
                            source=m.group(0)))
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
            # Grammar accessor varies: most expose language(); tree-sitter-typescript
            # exposes language_typescript()/language_tsx(). Try both so TS activates
            # instead of silently falling back to the regex heuristic.
            lang_fn = (getattr(mod, "language", None)
                       or getattr(mod, f"language_{language}", None)
                       or getattr(mod, "language_typescript", None))
            if lang_fn is None:
                raise TreeSitterUnavailable(f"{self._GRAMMAR[language]}: no language() accessor")
            self._lang = Language(lang_fn())
            self._parser = Parser(self._lang)
        except TreeSitterUnavailable:
            raise
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

        # Single scope-tracking walk (fix: nested definitions get their own scope, so a
        # call inside inner() is attributed to inner, not to the enclosing outer()).
        #   container_q      naming prefix (nearest enclosing def)
        #   func_q           call-attribution scope (nearest enclosing FUNCTION, or None)
        #   encl_is_class    is the nearest enclosing definition a class? (method vs function)
        def walk(node, container_q, func_q, encl_is_class):
            for child in node.children:
                t = child.type
                if t == "import_statement":
                    s = child.child_by_field_name("source")
                    if s:
                        edges.append(ParsedEdge(module_q, txt(s).strip("'\""),
                                                "IMPORTS", 0.85, "tree_sitter"))
                    walk(child, container_q, func_q, encl_is_class)
                elif t in self._CLASS:
                    nm = name_of(child)
                    if not nm:
                        walk(child, container_q, func_q, encl_is_class); continue
                    qn = f"{container_q}.{nm}"
                    syms.append(ParsedSymbol(qn, "class", rel_path, self.language,
                                child.start_point[0] + 1, child.end_point[0] + 1, nm,
                                content_hash(txt(child)), source=txt(child)))
                    edges.append(ParsedEdge(container_q, qn, "CONTAINS", 0.9, "tree_sitter"))
                    sc = (child.child_by_field_name("superclass")
                          or child.child_by_field_name("superclasses"))
                    if sc:
                        edges.append(ParsedEdge(qn, txt(sc).split()[-1].split(".")[-1],
                                                "IMPLEMENTS", 0.7, "tree_sitter"))
                    walk(child, qn, None, True)          # inside a class body
                elif t in self._FUNC:
                    nm = name_of(child)
                    if not nm:
                        walk(child, container_q, func_q, encl_is_class); continue
                    kind = "method" if encl_is_class else "function"
                    qn = f"{container_q}.{nm}"
                    syms.append(ParsedSymbol(qn, kind, rel_path, self.language,
                                child.start_point[0] + 1, child.end_point[0] + 1, nm,
                                content_hash(txt(child)), source=txt(child)))
                    edges.append(ParsedEdge(container_q, qn, "CONTAINS", 0.9, "tree_sitter"))
                    walk(child, qn, qn, False)           # inside a function body
                elif t == "call_expression":
                    fn = child.child_by_field_name("function")
                    if fn:
                        edges.append(ParsedEdge(func_q or module_q,
                                     txt(fn).split(".")[-1], "CALLS", 0.7, "tree_sitter"))
                    walk(child, container_q, func_q, encl_is_class)   # args may hold calls
                else:
                    walk(child, container_q, func_q, encl_is_class)

        walk(tree.root_node, module_q, None, False)
        return syms, edges
