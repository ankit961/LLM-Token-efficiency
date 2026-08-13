"""Phase 2.4-B — SemanticReadEvent telemetry backbone (observe-only).

A read emits a durable event; an expansion links to the read that caused it so Context
Expansion Debt sums directly. Classification/outcome columns stay null until 2.4-C.
"""
import sqlite3
import threading
from pathlib import Path

import pytest

from contextruntime.codegraph import builder
from contextruntime.ingest import est_tokens
from contextruntime.model import SemanticReadEvent
from contextruntime.semanticfs import context_expand, read_symbol
from contextruntime.store import GraphStore
from contextruntime.telemetry import record_expansion, record_read

REPO = Path(__file__).parent / "fixtures" / "bundle_repo"


def _store():
    s = GraphStore(":memory:")
    builder.index_path(s, str(REPO), "bundle")
    return s


def test_read_emits_durable_event():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    eid = record_read(s, rr, session_id="sess1", request_id="req1", repo_id="bundle")
    row = s.semantic_read(eid)
    assert row is not None
    assert row["channel"] == "semanticfs"
    assert row["session_id"] == "sess1" and row["request_id"] == "req1"
    assert row["symbol_id"] == rr.root
    assert row["semantic_payload_tokens"] == rr.budget["serialized_tokens"]
    assert row["source_body_tokens"] == rr.budget["source_body_tokens"]
    assert row["protocol_overhead"] == rr.budget["protocol_overhead_ratio"]
    assert row["budget_requested"] == 1000
    # no transport wrapper on a direct record_read -> transport == payload, zero overhead
    assert row["transport_content_tokens"] == row["semantic_payload_tokens"]
    assert row["transport_overhead_tokens"] == 0
    assert row["seq"] is not None and row["seq"] >= 1             # DB-assigned (AUTOINCREMENT)
    assert row["allowed"] == 1 and row["denied"] == 0            # observe-only
    assert row["observed_class"] is None                          # 2.4-C fills this
    assert row["path"].endswith("service.py")                     # provenance carried
    assert row["content_hash"]
    s.close()


def test_expansion_links_to_parent_and_sums_ced():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    parent = record_read(s, rr, session_id="sess1", request_id="req1", repo_id="bundle")
    exp = context_expand(s, f"ctx://symbol/{rr.root}@implementation")   # model escalates to body
    child = record_expansion(s, exp, parent_event_id=parent, session_id="sess1",
                             request_id="req2", from_level="identity", reason="need body")
    row = s.semantic_read(child)
    assert row["parent_event_id"] == parent
    assert row["channel"] == "expansion"
    assert row["to_level"] == "implementation" and row["from_level"] == "identity"
    # CED = FULL transport tokens of the expansion; a direct record_expansion has no transport
    # wrapper, so transport == est(exp.text) == semantic payload here.
    assert s.context_expansion_debt(parent) == est_tokens(exp.text)
    assert s.context_expansion_debt(parent) == row["transport_content_tokens"]
    assert row["semantic_payload_tokens"] == est_tokens(exp.text)
    s.close()


def test_not_found_expansion_records_nothing():
    s = _store()
    exp = context_expand(s, "ctx://symbol/nope")
    assert record_expansion(s, exp, parent_event_id="ev_x") is None
    assert s.semantic_reads() == []
    s.close()


# 2.4-B.1 (the collapse bug): two REAL materializations sharing session/request/channel/symbol
# must be two distinct rows — not silently merged. A content-hashed event_id would fail this.
def test_repeated_materialization_is_two_distinct_events():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    e1 = record_read(s, rr, session_id="S", request_id="R", channel="semanticfs")
    e2 = record_read(s, rr, session_id="S", request_id="R", channel="semanticfs")   # identical fields
    assert e1 != e2
    rows = s.semantic_reads()
    assert len(rows) == 2
    assert len({r["event_id"] for r in rows}) == 2
    assert len({r["seq"] for r in rows}) == 2
    s.close()


# Idempotence is INTENTIONAL: a replay carrying the same producer key dedups to one row and
# returns the canonical event_id — a duplicate DELIVERY, not a duplicate EVENT.
def test_source_event_key_makes_replay_idempotent():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    e1 = record_read(s, rr, session_id="S", source_system="claude_mcp", source_event_key="tool_use_1")
    e2 = record_read(s, rr, session_id="S", source_system="claude_mcp", source_event_key="tool_use_1")  # replay
    assert e2 == e1                                              # canonical id, not a new one
    assert len(s.semantic_reads()) == 1
    s.close()


# The producer key is NAMESPACED + SESSION-SCOPED: the same tool_use_id in two sessions is two
# events (the pre-B.1.2 global-unique key would have wrongly collapsed them).
def test_source_event_key_is_session_scoped():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    a = record_read(s, rr, session_id="A", source_system="claude_mcp", source_event_key="tool_use_1")
    b = record_read(s, rr, session_id="B", source_system="claude_mcp", source_event_key="tool_use_1")
    assert a != b
    assert len(s.semantic_reads()) == 2
    s.close()


# The mirror of the 100-distinct-events concurrency test: N concurrent DELIVERIES of ONE producer
# event → exactly one row, and every caller gets the SAME canonical id (never an unpersisted UUID).
def test_concurrent_replay_canonicalizes_to_one_id(tmp_path):
    db = str(tmp_path / "replay.db")
    init = GraphStore(db)
    builder.index_path(init, str(REPO), "bundle")
    init.commit()
    init.close()
    returned, lock = {}, threading.Lock()

    def worker(w):
        c = GraphStore(db)
        rr = read_symbol(c, "service.process", budget=1000)
        eid = record_read(c, rr, session_id="S", source_system="claude_mcp",
                          source_event_key="tool_use_42")
        c.commit()
        c.close()
        with lock:
            returned[w] = eid

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    r = GraphStore(db)
    rows = [x for x in r.semantic_reads() if x["source_event_key"] == "tool_use_42"]
    assert len(rows) == 1                                        # exactly one persisted row
    assert len(set(returned.values())) == 1                     # every caller got the SAME id
    assert rows[0]["event_id"] == next(iter(returned.values())) # and it is the persisted one
    r.close()


# An accidental event_id collision must FAIL LOUDLY, not be silently swallowed.
def test_duplicate_event_id_fails_loudly():
    s = _store()
    s.put_semantic_read(SemanticReadEvent(event_id="dup", channel="semanticfs"))
    with pytest.raises(sqlite3.IntegrityError):
        s.put_semantic_read(SemanticReadEvent(event_id="dup", channel="semanticfs"))
    s.close()


# issue 1: a materialization is logged even without a parent (attribution is optional).
def test_parentless_expansion_still_records():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    exp = context_expand(s, f"ctx://symbol/{rr.root}@implementation")
    eid = record_expansion(s, exp, session_id="s", request_id="q")   # NO parent_event_id
    row = s.semantic_read(eid)
    assert row is not None and row["channel"] == "expansion"
    assert row["parent_event_id"] is None                            # unattributed but still logged
    s.close()


# The CED distinction that matters: a parentless expansion is logged but attributes to NO read;
# a parented one contributes EXACTLY its transport_content_tokens to that read.
def test_parentless_expansion_excluded_from_ced():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    parent = record_read(s, rr, session_id="s", request_id="r1")
    orphan = context_expand(s, f"ctx://symbol/{rr.root}@implementation")
    record_expansion(s, orphan, session_id="s", request_id="r2")     # no parent → not this read's debt
    assert s.context_expansion_debt(parent) == 0
    child_exp = context_expand(s, f"ctx://symbol/{rr.root}@implementation")
    child = record_expansion(s, child_exp, parent_event_id=parent, session_id="s", request_id="r3")
    assert s.context_expansion_debt(parent) == s.semantic_read(child)["transport_content_tokens"] > 0
    s.close()


# The measurement identity (2.4-B.1): CED linkage is by event_id, and seq is a DB-assigned,
# concurrency-safe ORDER surrogate that never appears in linkage. Even with two connections
# interleaving writes to the same file (and threads racing), seqs stay unique + ordered and no
# event is lost — which SELECT MAX(seq)+1 could not guarantee.
def test_seq_is_concurrency_safe(tmp_path):
    db = str(tmp_path / "cc.db")
    GraphStore(db).close()                                           # initialise schema
    n_threads, per = 4, 25

    def worker(w):
        c = GraphStore(db)
        for i in range(per):
            c.put_semantic_read(SemanticReadEvent(event_id=f"w{w}-{i}", channel="semanticfs",
                                                  session_id="s", request_id=f"w{w}-{i}"))
            c.commit()
        c.close()

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    r = GraphStore(db)
    rows = r.semantic_reads()
    total = n_threads * per
    assert len(rows) == total                                       # no lost / overwritten event
    assert len({x["seq"] for x in rows}) == total                   # every seq unique (AUTOINCREMENT)
    assert [x["seq"] for x in rows] == sorted(x["seq"] for x in rows)  # strict DB ordering
    assert len({x["event_id"] for x in rows}) == total              # all event_ids preserved
    r.close()


def test_seq_is_monotonic_and_events_ordered():
    s = _store()
    r1 = read_symbol(s, "service.process", budget=500)
    r2 = read_symbol(s, "service.run_db", budget=500)
    e1 = record_read(s, r1, session_id="s", request_id="q1")
    e2 = record_read(s, r2, session_id="s", request_id="q2")
    rows = s.semantic_reads(session_id="s")
    assert [r["event_id"] for r in rows] == [e1, e2]              # ordered by seq
    assert rows[0]["seq"] < rows[1]["seq"]
    s.close()


def test_channel_and_bypass_are_recorded():
    # the 2.4-C classifier attributes native/bash reads; the schema already carries the channel
    s = _store()
    rr = read_symbol(s, "service.process", budget=500)
    record_read(s, rr, session_id="s", request_id="q", channel="bash_materialization",
                bypass_channel="cat")
    row = s.semantic_reads(channel="bash_materialization")[0]
    assert row["channel"] == "bash_materialization" and row["bypass_channel"] == "cat"
    s.close()
