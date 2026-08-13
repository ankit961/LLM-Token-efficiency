"""End-to-end smoke test: ingest a synthetic transcript -> residency graph -> ledger.

The fixture is fully synthetic (no private data). It exercises: request
reconciliation, content-object creation, a duplicate read (req_1 and req_3 return
the same file body -> DUPLICATE_OF), a cache break after a long gap (req_4 rebuilds),
and the occupancy/economic ledgers.
"""
from pathlib import Path

import pytest

from contextruntime import SCHEMA_VERSION
from contextruntime import ledger as ledger_mod
from contextruntime import doctor as doctor_mod
from contextruntime.residency import ingest_file
from contextruntime.store import GraphStore

FIX = Path(__file__).parent / "fixtures" / "synthetic_session.jsonl"


@pytest.fixture
def store():
    s = GraphStore(":memory:")
    ingest_file(s, FIX)
    yield s
    s.close()


def test_requests_reconciled(store):
    # four distinct requestIds -> four Request nodes
    assert store.count("requests") == 4


def test_objects_and_residency(store):
    assert store.count("objects") >= 4                 # texts + tool results
    assert store.edge_count("RESIDENT_IN") == store.count("objects")


def test_duplicate_read_detected(store):
    # the identical file body delivered at req_1 and req_3
    assert store.edge_count("DUPLICATE_OF") >= 1


def test_materialized_from_sources(store):
    assert store.edge_count("MATERIALIZED_FROM") >= 1
    assert store.count("sources") >= 1


def test_cache_break_after_gap(store):
    # req_4 reads ~0 cache after a big prefix -> a BROKE edge / new island
    assert store.count("islands") >= 2
    assert store.edge_count("BROKE") >= 1


def test_ledger_occupancy_exact(store):
    rep = ledger_mod.compute(store)
    # occupancy == sum of input + cache_read + cache_creation across requests
    # fixture: (8+0+25000)+(6+25000+300)+(6+25300+220)+(10+0+26000)
    assert rep.occupancy_tokens == 8 + 25000 + 6 + 25000 + 300 + 6 + 25300 + 220 + 10 + 26000
    assert rep.n_requests == 4
    assert rep.attributed_token_turns > 0
    assert rep.est_cost_usd > 0
    assert rep.duplicate_events >= 1


def test_schema_versioned(store):
    row = store.conn.execute(
        "SELECT schema_version FROM objects LIMIT 1").fetchone()
    assert row["schema_version"] == SCHEMA_VERSION


def test_doctor_stamp():
    prof = doctor_mod.probe()
    assert prof.evidence_grade == "C"          # C until adapters confirm capabilities
    assert "grade=C" in doctor_mod.stamp(prof)
    assert prof.capabilities["mcp_session_id"] == "no"
