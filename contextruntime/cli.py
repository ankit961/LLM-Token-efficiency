"""ContextRuntime CLI (Phase 0b).

    contextruntime ingest <transcript.jsonl ...> [--db graph.db]
    contextruntime ledger [--db graph.db] [--pricing pricing.json]
    contextruntime doctor
    contextruntime graph  [--db graph.db]        # node/edge counts

With no --db, an in-memory store is used (ingest + ledger in one shot).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from . import __version__
from . import crhook as crhook_mod
from . import doctor as doctor_mod
from . import labelreport as labelreport_mod
from . import ledger as ledger_mod
from .residency import ingest_file
from .store import GraphStore

DEFAULT_PROJECTS = os.path.expanduser("~/.claude/projects")


def _open(db: str | None) -> GraphStore:
    return GraphStore(db or ":memory:")


def cmd_ingest(args) -> int:
    store = _open(args.db)
    paths = []
    for p in args.paths:
        paths.extend(glob.glob(p, recursive=True) if any(c in p for c in "*?[") else [p])
    if not paths:
        print("no transcripts matched", file=sys.stderr)
        return 1
    totals = {"requests": 0, "events": 0, "segments": 0, "files": 0}
    for path in paths:
        if os.path.basename(path) == "journal.jsonl":
            continue
        try:
            r = ingest_file(store, path)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"[warn] {os.path.basename(path)}: {e}", file=sys.stderr)
            continue
        totals["files"] += 1
        for k in ("requests", "events", "segments"):
            totals[k] += r[k]
    print(f"ingested {totals['files']} files: "
          f"{totals['requests']:,} requests, {totals['events']:,} content objects, "
          f"{totals['segments']:,} segments")
    print(f"graph: objects={store.count('objects'):,} requests={store.count('requests'):,} "
          f"islands={store.count('islands'):,} "
          f"RESIDENT_IN={store.edge_count('RESIDENT_IN'):,} "
          f"DUPLICATE_OF={store.edge_count('DUPLICATE_OF'):,} "
          f"BROKE={store.edge_count('BROKE'):,}")
    if not args.db:
        _print_ledger(store, args_pricing=None)
    store.close()
    return 0


def _print_ledger(store, args_pricing):
    profile = doctor_mod.probe()
    rep = ledger_mod.compute(store, args_pricing)
    rep.evidence_grade = profile.evidence_grade
    print()
    print(ledger_mod.format_report(rep, doctor_mod.stamp(profile)))


def cmd_ledger(args) -> int:
    if not args.db or not os.path.exists(args.db):
        print("ledger needs an ingested --db (run `ingest --db ...` first)", file=sys.stderr)
        return 1
    store = _open(args.db)
    _print_ledger(store, args.pricing)
    store.close()
    return 0


def cmd_doctor(args) -> int:
    print(doctor_mod.format_report(doctor_mod.probe()))
    return 0


def cmd_graph(args) -> int:
    store = _open(args.db)
    for tbl in ("objects", "requests", "islands", "sources", "capsules"):
        print(f"  {tbl:10s} {store.count(tbl):,}")
    for et in ("RESIDENT_IN", "MATERIALIZED_FROM", "DUPLICATE_OF", "BROKE", "REDUCES"):
        print(f"  edge {et:18s} {store.edge_count(et):,}")
    store.close()
    return 0


def cmd_reduce_scan(args) -> int:
    """Experiment-B: measure what ContextReduce would save over an ingested graph."""
    from .reducers import planner
    if not args.db or not os.path.exists(args.db):
        print("reduce-scan needs an ingested --db (run `ingest --db ...` first)", file=sys.stderr)
        return 1
    store = _open(args.db)
    rep = planner.scan_graph(store, write_edges=not args.dry_run)
    print(planner.format_report(rep, evidence_grade=doctor_mod.probe().evidence_grade))
    store.close()
    return 0


def cmd_hook(args) -> int:
    """PostToolUse hook entry (reads a hook event on stdin). Fail-open."""
    from .reducers import hook as hook_mod
    return hook_mod.main()


def cmd_cr_hook(args) -> int:
    """Observation-layer hook entry (Phase 2.4-C): feed one hook delivery into a HookJournal.
    Observe-only, fail-open, always exits 0 -- distinct from the Phase-1 `hook` reducer."""
    from .crhook import run
    return run(sys.stdin.read(), args.db)


def cmd_label_report(args) -> int:
    """Slice 3A: observed-label validity report over a HookJournal (observe-only, aggregate-only).
    Canonical by default (primary=16, windows={8,16,32,inf}); experimentation is explicit + stamped."""
    import subprocess
    from . import labelreport
    if not args.db or not os.path.exists(args.db):
        print("label-report needs a HookJournal --db (capture one with `cr-hook` first)", file=sys.stderr)
        return 1
    manifest = None
    if args.manifest:
        with open(args.manifest) as fh:
            manifest = json.load(fh)
    canonical = not args.experimental_windows
    windows, primary = None, None
    if args.experimental_windows:
        windows = tuple(labelreport.INF_WINDOW if w.strip().lower() in ("inf", "infinity")
                        else int(w) for w in args.experimental_windows.split(","))
        primary = args.experimental_primary
    runtime_sha = None
    try:
        runtime_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip() or None
    except Exception:  # noqa: BLE001 -- provenance is best-effort
        runtime_sha = None
    try:
        rep = labelreport.build_report(args.db, manifest=manifest, canonical=canonical, windows=windows,
                                       primary=primary, runtime_commit_sha=runtime_sha,
                                       client_version=args.client_version,
                                       include_stream_map=bool(args.stream_map))
    except ValueError as e:
        print(f"label-report: {e}", file=sys.stderr)
        return 1
    if args.stream_map:                                       # raw map -> a LOCAL file, out of the artifact
        with open(args.stream_map, "w") as fh:
            json.dump(rep.pop("_stream_key_map", {}), fh, indent=2)
        print(f"[wrote raw stream-key map -- LOCAL ONLY, do not share: {args.stream_map}]", file=sys.stderr)
    print(labelreport.format_text(rep))
    if args.json:
        with open(args.json, "w") as fh:
            fh.write(labelreport.report_json(rep))
        print(f"\n[wrote JSON evidence artifact: {args.json}]", file=sys.stderr)
    return 0


def cmd_index_code(args) -> int:
    """Build the CodeSymbol graph (Graph-Lite) for a repository path."""
    from .codegraph import builder
    store = _open(args.db)
    rep = builder.index_path(store, args.path, repo_id=args.repo)
    print(builder.format_report(rep))
    store.close()
    return 0


def cmd_bundle(args) -> int:
    """Build a budgeted dependency bundle for a root symbol (Phase 2.2)."""
    from .codegraph import bundle as bundle_mod
    store = _open(args.db)
    root = args.root
    if not store.has_symbol(root):
        row = store.find_symbol(root, repo_id=args.repo)
        if row is None:
            print(f"root symbol not found: {root}", file=sys.stderr)
            store.close()
            return 1
        root = row["symbol_id"]
    b = bundle_mod.build_bundle(store, root, budget=args.budget, max_depth=args.max_depth)
    print(bundle_mod.format_bundle(b))
    store.close()
    return 0


def cmd_read_symbol(args) -> int:
    """SemanticFS read_symbol — rendered source-derived context under a budget."""
    from .semanticfs import read_symbol
    store = _open(args.db)
    rr = read_symbol(store, args.symbol, budget=args.budget, resolution=args.resolution,
                     repo_id=args.repo)
    if not rr.ok:
        print(rr.note, file=sys.stderr); store.close(); return 1
    b = rr.budget
    print(f"# read_symbol {rr.root}  resolution={rr.resolution}")
    print(f"# budget serialized={b['serialized_tokens']}/{b['requested']} "
          f"body={b['source_body_tokens']} overhead={b['protocol_overhead_ratio']:.0%} "
          f"PRE={b['planned_vs_rendered_error']} shrink={b['shrink_ratio']}"
          + ("  [budget_insufficient]" if b['budget_insufficient'] else ""))
    print(f"# graph {rr.graph}   expand→next: {rr.expansion.get('next')}")
    print()
    print(rr.to_text())
    store.close()
    return 0


def cmd_find_callers(args) -> int:
    from .semanticfs import find_callers
    store = _open(args.db)
    for c in find_callers(store, args.symbol, repo_id=args.repo):
        print(f"  {c['qualified_name']:40s} {c['path']:24s} {c['match']:10s} {c['handle']}")
    store.close()
    return 0


def cmd_search(args) -> int:
    from .semanticfs import context_search
    store = _open(args.db)
    for r in context_search(store, args.query, repo_id=args.repo, limit=args.limit):
        print(f"  {r['qualified_name']:40s} {r['kind']:10s} {r['path']:24s} {r['handle']}")
    store.close()
    return 0


def cmd_mcp(args) -> int:
    """Serve the SemanticFS read surface over MCP stdio (observe-only telemetry)."""
    from .mcp import main as mcp_main
    argv = ["--db", args.db]
    if args.repo:
        argv += ["--repo", args.repo]
    if args.session:
        argv += ["--session", args.session]
    return mcp_main(argv)


def cmd_expand(args) -> int:
    """Resolve a result:// or ctx://symbol handle back to its payload (SemanticFS)."""
    from .semanticfs import context_expand
    store = _open(args.db)
    exp = context_expand(store, args.handle)
    if not exp.found:
        print(f"{args.handle}: {exp.note}", file=sys.stderr)
        store.close()
        return 1
    print(f"# {args.handle}  kind={exp.kind}  bytes={exp.byte_size}  ({exp.note})")
    print(exp.text)
    store.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="contextruntime",
                                 description=f"ContextRuntime Phase 0b (v{__version__})")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="ingest transcripts into the residency graph")
    p.add_argument("paths", nargs="*",
                   default=[os.path.join(DEFAULT_PROJECTS, "*", "*.jsonl")])
    p.add_argument("--db", help="sqlite path (default: in-memory, prints ledger)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ledger", help="compute occupancy + economic ledgers")
    p.add_argument("--db", required=True)
    p.add_argument("--pricing")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("doctor", help="probe runtime capabilities")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("graph", help="node/edge counts")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("reduce-scan", help="measure ContextReduce savings (Experiment B, observe mode)")
    p.add_argument("--db", required=True)
    p.add_argument("--dry-run", action="store_true", help="do not write REDUCES edges")
    p.set_defaults(func=cmd_reduce_scan)

    p = sub.add_parser("hook", help="PostToolUse hook entry (reads a hook event on stdin)")
    p.add_argument("event", nargs="?", choices=["post"], default="post")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("cr-hook",
                       help="observation-layer hook: record one hook delivery into a HookJournal (fail-open)")
    p.add_argument("--db", default=os.environ.get("CR_HOOK_DB", crhook_mod.DEFAULT_JOURNAL),
                   help="journal sqlite path (default: $CR_HOOK_DB or ~/.claude/contextruntime/hookjournal.db)")
    p.set_defaults(func=cmd_cr_hook)

    p = sub.add_parser("read-symbol", help="SemanticFS: rendered source-derived context under a budget")
    p.add_argument("symbol")
    p.add_argument("--db", required=True)
    p.add_argument("--budget", type=int, default=2048)
    p.add_argument("--resolution", default="adaptive",
                   choices=["adaptive", "identity", "signature", "skeleton", "slice", "implementation"])
    p.add_argument("--repo")
    p.set_defaults(func=cmd_read_symbol)

    p = sub.add_parser("find-callers", help="reverse CALLS traversal for a symbol")
    p.add_argument("symbol"); p.add_argument("--db", required=True); p.add_argument("--repo")
    p.set_defaults(func=cmd_find_callers)

    p = sub.add_parser("search", help="context_search — symbol references as handles (no code dumps)")
    p.add_argument("query"); p.add_argument("--db", required=True)
    p.add_argument("--repo"); p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("expand", help="resolve a result:// or ctx://symbol handle (SemanticFS)")
    p.add_argument("--db", required=True)
    p.add_argument("handle")
    p.set_defaults(func=cmd_expand)

    p = sub.add_parser("mcp", help="serve the SemanticFS read surface over MCP stdio (observe-only)")
    p.add_argument("--db", required=True, help="sqlite store (telemetry persisted here)")
    p.add_argument("--repo", help="default repo_id for reads")
    p.add_argument("--session", help="session id (default: generated)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("label-report",
                       help="Slice 3A: observed-label validity report over a HookJournal (canonical, observe-only)")
    p.add_argument("--db", required=True, help="HookJournal sqlite path (from cr-hook)")
    p.add_argument("--manifest", help="JSON closure manifest: {\"streams\":[{stream_key,closed,closure_reason,closed_at_seq}]}")
    p.add_argument("--json", help="also write the JSON evidence artifact to this path")
    p.add_argument("--experimental-windows",
                   help="NON-canonical: comma list e.g. 8,16,32,inf,64 (stamps canonical_report=false)")
    p.add_argument("--experimental-primary", type=int, default=labelreport_mod.PRIMARY_WINDOW,
                   help="primary window when --experimental-windows is set (default 16; must be in the set)")
    p.add_argument("--stream-map", help="write the raw pseudonym->stream_key map to this LOCAL file (not shared)")
    p.add_argument("--client-version", help="Claude Code client version stamp (one DB must be one client)")
    p.set_defaults(func=cmd_label_report)

    p = sub.add_parser("index-code", help="build the CodeSymbol graph (Graph-Lite) for a repo")
    p.add_argument("path")
    p.add_argument("--db", required=True)
    p.add_argument("--repo", help="repo id (default: dir name)")
    p.set_defaults(func=cmd_index_code)

    p = sub.add_parser("bundle", help="build a budgeted dependency bundle for a root symbol")
    p.add_argument("root", help="symbol_id or qualified_name")
    p.add_argument("--db", required=True)
    p.add_argument("--budget", type=int, default=2048)
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--repo")
    p.set_defaults(func=cmd_bundle)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
