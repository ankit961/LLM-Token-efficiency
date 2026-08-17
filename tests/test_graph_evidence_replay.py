"""Step-6.1 tests — prove the in-memory proximity is faithful to gr._proximity, and check the
line-retention primitives. Zero quota, no GraphStore needed (fake store over identical edges)."""
import json
from collections import defaultdict

import pytest

from contextruntime.reducers import graphrank as gr
from corpus.graph_evidence_replay import (_first_line_rank, _has_line, _proximity_inmemory,
                                          load_task_graphs)


class _FakeStore:
    """Minimal store exposing exactly the two edge queries gr._proximity uses, backed by an edge
    list — so gr._proximity and _proximity_inmemory run over provably identical edges."""
    def __init__(self, edges):
        self.frm, self.to = defaultdict(list), defaultdict(list)
        for s, d, e, c in edges:
            self.frm[s].append({"dst_id": d, "edge_type": e, "confidence": c})
            self.to[d].append({"src_id": s, "edge_type": e, "confidence": c})

    def code_edges_from(self, sid, edge_types=None):
        return [e for e in self.frm.get(sid, []) if not edge_types or e["edge_type"] in edge_types]

    def code_edges_to(self, sid, edge_types=None):
        return [e for e in self.to.get(sid, []) if not edge_types or e["edge_type"] in edge_types]


def _adj(edges):
    adj = defaultdict(list)
    for s, d, e, c in edges:
        adj[s].append((d, e, c))
        adj[d].append((s, e, c))
    return adj


def test_inmemory_proximity_matches_graphrank_exactly():
    # a multi-hop graph with a cycle and mixed edge types / confidences
    edges = [
        ("A", "B", "CALLS", 1.0), ("B", "C", "IMPORTS", 1.0), ("C", "D", "DEPENDS_ON", 0.9),
        ("A", "E", "TESTED_BY", 0.5), ("E", "F", "CALLS", 1.0), ("F", "A", "IMPLEMENTS", 0.8),
        ("C", "F", "DEPENDS_ON", 0.7), ("D", "G", "CALLS", 1.0),
    ]
    fake, adj = _FakeStore(edges), _adj(edges)
    for anchors in ({"A"}, {"A", "D"}, {"G"}, {"B", "F"}, set()):
        assert _proximity_inmemory(adj, anchors) == gr._proximity(fake, anchors), anchors


def test_inmemory_proximity_respects_max_depth():
    # a chain longer than MAX_DEPTH — both implementations must cut at the same hop
    edges = [(chr(65 + i), chr(66 + i), "CALLS", 1.0) for i in range(gr.MAX_DEPTH + 3)]
    fake, adj = _FakeStore(edges), _adj(edges)
    assert _proximity_inmemory(adj, {"A"}) == gr._proximity(fake, {"A"})


def test_line_primitives_are_component_aware():
    kept = ["django/db/models/query.py:10: def x", "a/b/c.py:3: y"]
    assert _has_line(kept, "django/db/models/query.py")
    assert _has_line(kept, "/wt/a/b/c.py")                       # abs suffix still matches
    assert not _has_line(kept, "django/db/models/other.py")     # different file, not conflated
    assert _first_line_rank(kept, "a/b/c.py") == 1
    assert _first_line_rank(kept, "z/z.py") == 10 ** 6           # absent ⇒ large sentinel


def test_load_task_graphs_fails_loud_on_provenance_mismatch(tmp_path):
    # graph provenance says base_commit X, but the independent manifest says Y ⇒ REFUSE (fail loud),
    # never rank against an unverified graph. Deterministic: no real GraphStore needed.
    task = "9999"
    cg = tmp_path / f"django__django-{task}" / "C_graph"
    cg.mkdir(parents=True)
    db = cg / "codegraph.db"
    db.write_text("not-a-real-db")
    json.dump({"base_commit": "X" * 40, "graph_db_sha256": "unmatched"},
              open(str(db) + ".provenance.json", "w"))
    arm = tmp_path / f"django__django-{task}" / "A_native"
    arm.mkdir(parents=True)
    json.dump({"base_commit": "Y" * 40}, open(arm / "manifest.json", "w"))
    with pytest.raises(RuntimeError, match="provenance FAILED"):
        load_task_graphs(str(tmp_path), [task])
