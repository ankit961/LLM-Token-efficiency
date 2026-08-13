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
import os
import sys

from . import __version__
from . import doctor as doctor_mod
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
    for et in ("RESIDENT_IN", "MATERIALIZED_FROM", "DUPLICATE_OF", "BROKE"):
        print(f"  edge {et:18s} {store.edge_count(et):,}")
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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
