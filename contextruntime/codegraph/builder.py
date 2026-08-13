"""Index a repository into the CodeSymbol graph.

Two passes: (1) parse every file into symbols + raw edges; (2) resolve each edge's
target name to a symbol_id where possible. Unresolved targets become
``unresolved:<name>`` and their confidence is discounted — so a dependency bundle
knows which edges are solid and which are guesses (design C3).
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
MAX_FILE_BYTES = 1_500_000
UNRESOLVED_DISCOUNT = 0.7          # multiply confidence when target is unresolved


@dataclass
class IndexReport:
    repo_id: str = ""
    files: int = 0
    symbols_by_language: dict = field(default_factory=dict)
    parser_by_language: dict = field(default_factory=dict)
    edges_by_type: dict = field(default_factory=dict)
    edges: int = 0
    resolved_edges: int = 0
    # per-language mean confidence + resolved-call rate (the C3 quality signal)
    quality_by_language: dict = field(default_factory=dict)
    resolution_by_source: dict = field(default_factory=dict)

    @property
    def resolved_rate(self) -> float:
        return self.resolved_edges / self.edges if self.edges else 0.0


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            yield os.path.join(dirpath, f)


def index_path(store: GraphStore, root: str, repo_id: str | None = None) -> IndexReport:
    root = os.path.abspath(root)
    repo_id = repo_id or os.path.basename(root.rstrip("/")) or "repo"
    store.delete_repo(repo_id)                       # idempotent re-index

    rep = IndexReport(repo_id=repo_id)
    sym_by_lang: Counter = Counter()
    parser_by_lang: dict = {}
    # pass 1: parse -> symbols + raw edges
    parsed_edges = []                                # (rel, src_qname, dst_name, type, conf, res)
    name_index: dict[str, list[str]] = defaultdict(list)   # short/qualified name -> [symbol_id]
    conf_acc: dict[str, list] = defaultdict(list)          # language -> [confidence...]

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
        syms, edges = adapter.parse(rel, source)
        if not syms:
            continue
        rep.files += 1
        parser_by_lang[adapter.language] = adapter.parser
        for s in syms:
            sid = f"{repo_id}::{s.path}::{s.qualified_name}"
            store.put_symbol(CodeSymbol(
                symbol_id=sid, repo_id=repo_id, language=s.language, kind=s.kind,
                qualified_name=s.qualified_name, path=s.path, start_line=s.start_line,
                end_line=s.end_line, signature=s.signature, content_hash=s.content_hash,
                parser=adapter.parser, resolution_quality=adapter.resolution_quality,
                schema_version=SCHEMA_VERSION))
            sym_by_lang[s.language] += 1
            name_index[s.qualified_name].append(sid)
            name_index[s.qualified_name.rsplit(".", 1)[-1]].append(sid)  # short name
        for e in edges:
            src_id = (f"{repo_id}::{rel}::{e.src_qname}" if e.src_qname else
                      f"{repo_id}::{rel}::{_module(rel)}")
            parsed_edges.append((repo_id, src_id, e.dst_name, e.edge_type,
                                 e.confidence, e.resolution, adapter.language))

    # pass 2: resolve targets to symbol_ids
    res_by_source: Counter = Counter()
    edge_type_ct: Counter = Counter()
    for repo, src_id, dst_name, etype, conf, res, lang in parsed_edges:
        candidates = name_index.get(dst_name) or name_index.get(dst_name.rsplit(".", 1)[-1])
        if candidates:
            dst_id, resolved = candidates[0], True
        else:
            dst_id, resolved = f"unresolved:{dst_name}", False
            conf = round(conf * UNRESOLVED_DISCOUNT, 3)
        store.add_code_edge(repo, src_id, dst_id, etype, conf, res)
        rep.edges += 1
        rep.resolved_edges += int(resolved)
        edge_type_ct[etype] += 1
        res_by_source[res] += 1
        conf_acc[lang].append(conf)
        # derived DEPENDS_ON for resolved CALLS/IMPLEMENTS/IMPORTS (bundle input, item 5)
        if resolved and etype in ("CALLS", "IMPLEMENTS", "IMPORTS"):
            store.add_code_edge(repo, src_id, dst_id, "DEPENDS_ON", conf, "derived")
            edge_type_ct["DEPENDS_ON"] += 1
    store.commit()

    rep.symbols_by_language = dict(sym_by_lang)
    rep.parser_by_language = parser_by_lang
    rep.edges_by_type = dict(edge_type_ct)
    rep.resolution_by_source = dict(res_by_source)
    rep.quality_by_language = {
        lang: {"mean_confidence": round(sum(cs) / len(cs), 3), "edges": len(cs)}
        for lang, cs in conf_acc.items()}
    return rep


def _module(rel: str) -> str:
    return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def format_report(rep: IndexReport) -> str:
    lines = [f"CodeSymbol graph — repo '{rep.repo_id}'",
             f"  files parsed : {rep.files:,}",
             f"  symbols      : {sum(rep.symbols_by_language.values()):,} "
             f"{dict(rep.symbols_by_language)}",
             f"  parsers      : {rep.parser_by_language}",
             f"  edges        : {rep.edges:,}  ({100*rep.resolved_rate:.0f}% resolved)  "
             f"{dict(rep.edges_by_type)}",
             "",
             "  bundle quality by language (mean edge confidence — the C3 signal):"]
    for lang, q in sorted(rep.quality_by_language.items()):
        lines.append(f"    {lang:12s} conf={q['mean_confidence']:.2f}  edges={q['edges']:,}  "
                     f"parser={rep.parser_by_language.get(lang, '?')}")
    lines += ["", "  by resolution source:"]
    for src, n in sorted(rep.resolution_by_source.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {src:16s} {n:,}")
    all_langs = available_adapters()
    lines += ["", f"  adapters available: {all_langs}"]
    return "\n".join(lines)
