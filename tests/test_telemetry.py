"""Phase 2.4-B — SemanticReadEvent telemetry backbone (observe-only).

A read emits a durable event; an expansion links to the read that caused it so Context
Expansion Debt sums directly. Classification/outcome columns stay null until 2.4-C.
"""
import threading
from pathlib import Path

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
