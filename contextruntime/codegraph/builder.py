"""Index a repository into the CodeSymbol graph.

Two passes: (1) parse every file into symbols + raw edges under a package-qualified
module name; (2) resolve each edge's target with an explicit precedence and an
AMBIGUITY state — never pick candidates[0]. A dependency bundle can then trust
`exact`/`scoped` edges, treat `inferred` as a single-candidate guess, and refuse to
invent a dependency from `ambiguous`/`unresolved` (design C1/C3, Phase 2.1).
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .. import SCHEMA_VERSION
from ..model import CodeSymbol
from ..store import GraphStore
from .registry import available_adapters, get_adapter

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
             "build", ".next", "target", ".pytest_cache"}
SOURCE_ROOTS = {"src"}                 # stripped from module paths (heuristic)
MAX_FILE_BYTES = 1_500_000
UNRESOLVED_DISCOUNT = 0.7
AMBIGUOUS_DISCOUNT = 0.45
# HARD: the call is actually resolved (a bundle may put these in the MANDATORY set).
# SOFT: `inferred` means "exactly one repo symbol has this short name" — a single-
#   candidate GUESS (the receiver could be external/injected), so it is derived as a
#   DEPENDS_ON but marked soft; the bundle generator must never make it mandatory.
# ambiguous/unresolved: no DEPENDS_ON at all.
HARD = {"exact", "scoped"}
SOFT = {"inferred"}
DEPENDABLE = HARD | SOFT


def module_qname(rel_path: str, source_roots=SOURCE_ROOTS) -> str:
    """Package-qualified module name so payments/utils.py != users/utils.py."""
    parts = rel_path.replace("\\", "/").split("/")
    while len(parts) > 1 and parts[0] in source_roots:
        parts = parts[1:]
    stem = parts[-1].rsplit(".", 1)[0]
    parts = parts[:-1] if stem == "__init__" else parts[:-1] + [stem]
    return ".".join(parts) if parts else stem


@dataclass
class IndexReport:
    repo_id: str = ""
    files: int = 0
    symbols_by_language: dict = field(default_factory=dict)
    parser_by_language: dict = field(default_factory=dict)
    edges_by_type: dict = field(default_factory=dict)
    edges: int = 0
    match_by_kind: dict = field(default_factory=dict)
    resolution_by_source: dict = field(default_factory=dict)
    structural_confidence_by_language: dict = field(default_factory=dict)

    @property
    def hard_rate(self) -> float:
        good = sum(self.match_by_kind.get(k, 0) for k in HARD)
        return good / self.edges if self.edges else 0.0

    @property
    def soft_rate(self) -> float:
        good = sum(self.match_by_kind.get(k, 0) for k in SOFT)
        return good / self.edges if self.edges else 0.0


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            yield os.path.join(dirpath, f)


def index_path(store: GraphStore, root: str, repo_id: str | None = None) -> IndexReport:
    root = os.path.abspath(root)
    repo_id = repo_id or os.path.basename(root.rstrip("/")) or "repo"
    store.delete_repo(repo_id)

    rep = IndexReport(repo_id=repo_id)
    sym_by_lang: Counter = Counter()
    conf_acc: dict = defaultdict(list)

    # symbol tables for resolution
    by_qname: dict[str, str] = {}                 # qualified_name -> symbol_id
    by_short: dict[str, list] = defaultdict(list)  # short name -> [symbol_id]
    module_members: dict[str, dict] = defaultdict(dict)   # module_q -> {short: symbol_id}
    class_members: dict[str, dict] = defaultdict(dict)    # class_q  -> {short: symbol_id}
    info_by_id: dict[str, tuple] = {}             # symbol_id -> (qname, kind, module_q)
    raw_edges: list = []                          # (src_id, src_module, src_class, dst, type, conf, res, lang)

    def sid(path, qn):
        return f"{repo_id}::{path}::{qn}"

    for fpath in _iter_files(root):
        adapter = get_adapter(fpath)
        if adapter is None:
            continue
        try:
            if os.path.getsize(fpath) > MAX_FILE_BYTES:
                continue
            source = open(fpath, "r", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(fpath, root)
        mod_q = module_qname(rel)
        syms, edges = adapter.parse(rel, source, mod_q)
        if not syms:
            continue
        rep.files += 1
        rep.parser_by_language[adapter.language] = adapter.parser
        for s in syms:
            s_id = sid(rel, s.qualified_name)
            store.put_symbol(CodeSymbol(
                symbol_id=s_id, repo_id=repo_id, language=s.language, kind=s.kind,
                qualified_name=s.qualified_name, path=rel, start_line=s.start_line,
                end_line=s.end_line, signature=s.signature, content_hash=s.content_hash,
                parser=adapter.parser, resolution_quality=adapter.resolution_quality,
                schema_version=SCHEMA_VERSION))
            sym_by_lang[s.language] += 1
            short = s.qualified_name.rsplit(".", 1)[-1]
            by_qname[s.qualified_name] = s_id
            by_short[short].append(s_id)
            info_by_id[s_id] = (s.qualified_name, s.kind, mod_q)
            if s.kind in ("function", "class", "method", "test"):
                parent = s.qualified_name.rsplit(".", 1)[0]
                (class_members[parent] if s.kind in ("method", "test")
                 else module_members[mod_q])[short] = s_id
        for e in edges:
            src_id = sid(rel, e.src_qname) if e.src_qname else sid(rel, mod_q)
            src_class = e.src_qname.rsplit(".", 1)[0] if e.src_qname.count(".") >= 1 else None
            raw_edges.append((src_id, mod_q, src_class, e.dst_name, e.edge_type,
                              e.confidence, e.resolution, adapter.language))

    # pass 2 — resolve with precedence + ambiguity
    match_ct: Counter = Counter()
    etype_ct: Counter = Counter()
    res_ct: Counter = Counter()
    for src_id, src_mod, src_class, dst_name, etype, conf, res, lang in raw_edges:
        dst_id, match, ambig, c = _resolve(
            dst_name, src_mod, src_class, by_qname, by_short, module_members, class_members, conf)
        store.add_code_edge(repo_id, src_id, dst_id, etype, c, res,
                            match_kind=match, ambiguity_count=ambig)
        rep.edges += 1
        match_ct[match] += 1
        etype_ct[etype] += 1
        res_ct[res] += 1
        conf_acc[lang].append(c)
        if match in DEPENDABLE and etype in ("CALLS", "IMPLEMENTS", "IMPORTS"):
            # inferred is a soft candidate, not a hard dependency (never mandatory)
            props = {"soft": True} if match in SOFT else None
            store.add_code_edge(repo_id, src_id, dst_id, "DEPENDS_ON", c, "derived",
                                match_kind=match, ambiguity_count=ambig, props=props)
            etype_ct["DEPENDS_ON"] += 1
    store.commit()

    rep.symbols_by_language = dict(sym_by_lang)
    rep.edges_by_type = dict(etype_ct)
    rep.match_by_kind = dict(match_ct)
    rep.resolution_by_source = dict(res_ct)
    rep.structural_confidence_by_language = {
        lang: {"mean_confidence": round(sum(cs) / len(cs), 3), "edges": len(cs)}
        for lang, cs in conf_acc.items()}
    return rep


def _resolve(dst_name, src_mod, src_class, by_qname, by_short,
             module_members, class_members, base_conf):
    """Return (dst_id, match_kind, ambiguity_count, confidence)."""
    # 1. exact qualified match
    if dst_name in by_qname:
        return by_qname[dst_name], "exact", 0, base_conf
    short = dst_name.rsplit(".", 1)[-1]
    # 2. same class scope (self.method / sibling)
    if src_class and short in class_members.get(src_class, {}):
        return class_members[src_class][short], "scoped", 0, base_conf
    # 3. same module scope
    if short in module_members.get(src_mod, {}):
        return module_members[src_mod][short], "scoped", 0, base_conf
    # 4. repo-wide short name
    cands = by_short.get(short, [])
    if len(cands) == 1:
        return cands[0], "inferred", 1, round(base_conf * 0.9, 3)
    if len(cands) > 1:
        # DO NOT choose one — record ambiguity, do not derive a dependency
        return f"ambiguous:{short}", "ambiguous", len(cands), round(base_conf * AMBIGUOUS_DISCOUNT, 3)
    # 5. unresolved (external import, dynamic dispatch, etc.)
    return f"unresolved:{short}", "unresolved", 0, round(base_conf * UNRESOLVED_DISCOUNT, 3)


def format_report(rep: IndexReport) -> str:
    lines = [
        f"Structural Confidence Report — repo '{rep.repo_id}'",
        "  (confidence is the runtime's ASSIGNED prior, NOT measured precision/recall;",
        "   empirical quality comes from the Gate-2A ground-truth dataset.)",
        f"  files parsed : {rep.files:,}",
        f"  symbols      : {sum(rep.symbols_by_language.values()):,} {dict(rep.symbols_by_language)}",
        f"  parsers      : {rep.parser_by_language}",
        f"  edges        : {rep.edges:,}  "
        f"({100*rep.hard_rate:.0f}% hard, {100*rep.soft_rate:.0f}% soft)  {dict(rep.edges_by_type)}",
        "",
        "  resolution match kinds (hard=exact/scoped · soft=inferred · none=ambiguous/unresolved):",
    ]
    for k, n in sorted(rep.match_by_kind.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {k:12s} {n:,}")
    lines += ["", "  structural confidence by language (assigned prior):"]
    for lang, q in sorted(rep.structural_confidence_by_language.items()):
        lines.append(f"    {lang:12s} conf={q['mean_confidence']:.2f}  edges={q['edges']:,}  "
                     f"parser={rep.parser_by_language.get(lang, '?')}")
    lines += ["", f"  adapters available: {available_adapters()}"]
    return "\n".join(lines)
