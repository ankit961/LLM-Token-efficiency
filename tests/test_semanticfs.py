"""Phase 2.3 — SemanticFS read surface: materializer (content monotonicity),
read_symbol (real source within RENDERED budget), read_slice, find_callers,
context_search (handles not dumps), context_expand (progressive), PRE metric.
"""
from pathlib import Path

from contextruntime import SCHEMA_VERSION
from contextruntime.codegraph import builder
from contextruntime.codegraph.render import render_symbol
from contextruntime.ingest import est_tokens
from contextruntime.model import CodeSymbol, content_hash
from contextruntime.semanticfs import (context_expand, context_search, find_callers,
                                       read_slice, read_symbol)
from contextruntime.store import GraphStore

REPO = Path(__file__).parent / "fixtures" / "bundle_repo"


def _store():
    s = GraphStore(":memory:")
    builder.index_path(s, str(REPO), "bundle")
    return s


def _sid(s, qn):
    return s.find_symbol(qn, "bundle")["symbol_id"]


# content monotonicity: lines(L1) ⊆ lines(L2) ⊆ lines(L3) ⊆ lines(L4)
def test_content_monotonicity():
    s = _store()
    row = s.symbol_row(_sid(s, "service.run_db"))     # a multi-line function
    sets = [render_symbol(s, row, lv).included_lines
            for lv in ("signature", "skeleton", "slice", "implementation")]
    for lo, hi in zip(sets, sets[1:]):
        assert lo <= hi                                # strictly nested
    assert sets[0] < sets[-1]                          # signature is smaller than impl
    s.close()


# read_symbol returns REAL source-derived text, not metadata
def test_read_symbol_returns_real_source():
    s = _store()
    rr = read_symbol(s, "service.process", budget=2048)
    assert rr.ok
    txt = rr.to_text()
    assert "def process" in txt                        # actual code from the fixture
    assert "validate" in txt                           # a real dependency call
    # provenance travels with each section
    root = rr.sections[0]
    assert root["provenance"]["path"].endswith("service.py")
    assert root["provenance"]["content_hash"]
    s.close()


# forgiving resolution — an agent can ask by a NATURAL (bare) name, so a steered read_symbol
# call succeeds instead of erroring back to a raw Read (the adoption blocker for ContextPolicy).
def test_forgiving_resolution_by_bare_name():
    s = _store()
    assert read_symbol(s, "service.process", budget=2048).ok        # exact qname still works
    rr = read_symbol(s, "process", budget=2048)                     # bare name -> qualified suffix
    assert rr.ok and "def process" in rr.to_text()
    s.close()


def test_forgiving_resolution_still_misses_the_truly_absent():
    s = _store()
    assert not read_symbol(s, "does_not_exist_anywhere", budget=512).ok
    s.close()


# SERIALIZED budget invariant — the model-visible payload (headers + handles +
# annotations + bodies), not merely the source bodies (2.3.1 P0).
def test_rendered_budget_respected():
    s = _store()
    for B in (60, 120, 300, 1000):
        rr = read_symbol(s, "service.process", budget=B)
        assert rr.budget["serialized_tokens"] <= B          # the reported number
        assert est_tokens(rr.to_text()) <= B                # the ACTUAL serialized text
    s.close()


# fixed-resolution reads are budget-enforced too (2.3.1 P0) — no bypass.
def test_fixed_resolution_budget_enforced():
    s = _store()
    # ask for full implementation but starve the budget: it must downgrade to fit.
    rr = read_symbol(s, "service.process", budget=40, resolution="implementation")
    assert est_tokens(rr.to_text()) <= 40
    assert rr.budget["serialized_tokens"] <= 40
    from contextruntime.semanticfs import DOWNGRADE
    assert DOWNGRADE.index(rr.sections[0]["level"]) < DOWNGRADE.index("implementation")
    s.close()


# read_slice honors its budget as a hard ceiling (2.3.1 P0).
def test_read_slice_budget_enforced():
    s = _store()
    rr = read_slice(s, "service.run_db", budget=512)
    assert est_tokens(rr.to_text()) <= 512
    s.close()


# Progressive expansion (2.4): the default hint is the NEXT level up, not the full body.
def test_progressive_expansion_next_not_full():
    from contextruntime.semanticfs import DOWNGRADE
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)
    assert rr.expansion["hint"] == "next"
    for sec in rr.sections:
        exp = sec["expansion"]
        assert exp["current"] == sec["level"]
        assert exp["full"].endswith("@implementation")
        idx = DOWNGRADE.index(sec["level"])
        if idx + 1 < len(DOWNGRADE):
            assert exp["next"].endswith("@" + DOWNGRADE[idx + 1])   # one step, not a jump to full
            assert not exp["next"].endswith("@implementation") or DOWNGRADE[idx + 1] == "implementation"
        else:
            assert exp["next"] is None                              # already at implementation
    # the read's default advertised handles are the NEXT ones, never @file
    assert all("@file" not in h for h in rr.expansion["next"])
    s.close()


# @file escalation is rejected until whole-file materialization actually exists (2.4 API fix).
def test_file_level_is_rejected():
    s = _store()
    sid = _sid(s, "service.run_db")
    exp = context_expand(s, f"ctx://symbol/{sid}@file")
    assert not exp.found and "unsupported" in exp.note and "file" in exp.note
    # a valid @implementation still works (only @file is gated)
    ok = context_expand(s, f"ctx://symbol/{sid}@implementation")
    assert ok.found
    s.close()


# Protocol overhead is MEASURED (2.4) so handle compaction is an evidence call, not a guess.
def test_protocol_overhead_measured():
    s = _store()
    small = read_symbol(s, "service.process", budget=120)
    large = read_symbol(s, "service.process", budget=2048)
    for rr in (small, large):
        b = rr.budget
        assert 0.0 <= b["protocol_overhead_ratio"] <= 1.0
        expect = round((b["serialized_tokens"] - b["source_body_tokens"]) / b["serialized_tokens"], 4)
        assert abs(b["protocol_overhead_ratio"] - expect) < 1e-9
    # the honest finding: verbose handles dominate a tiny budget more than a roomy one
    assert small.budget["protocol_overhead_ratio"] >= large.budget["protocol_overhead_ratio"]
    s.close()


# Below the irreducible per-section header floor the read is FLAGGED, not silently over budget.
def test_budget_below_header_floor_is_flagged():
    s = _store()
    rr = read_symbol(s, "service.process", budget=5, resolution="implementation")
    assert rr.budget["budget_insufficient"] is True
    assert "insufficient" in rr.note
    assert rr.sections[0]["level"] == "identity"            # minimal form, never a full body
    s.close()


# shrink validator: a tight budget forces a downgrade but stays within budget
def test_shrink_downgrades_and_fits():
    s = _store()
    big = read_symbol(s, "service.process", budget=1000)
    tight = read_symbol(s, "service.process", budget=90)
    assert tight.budget["serialized_tokens"] <= 90
    assert est_tokens(tight.to_text()) <= 90
    # the root is represented at a level no higher than in the roomy bundle
    from contextruntime.semanticfs import DOWNGRADE
    assert DOWNGRADE.index(tight.sections[0]["level"]) <= DOWNGRADE.index(big.sections[0]["level"])
    s.close()


# PRE measures ESTIMATOR error alone; deliberate shrinking is reported separately (2.3.1 P0).
def test_pre_isolated_from_shrink():
    s = _store()
    rr = read_symbol(s, "service.process", budget=1000)         # roomy: little/no shrink
    b = rr.budget
    assert b["estimator"] == "chars4-v1"
    # PRE is |planned − materialized_before| / materialized_before, both PRE-shrink
    expect = abs(b["planned_estimate"] - b["materialized_tokens"]) / max(1, b["materialized_tokens"])
    assert abs(b["planned_vs_rendered_error"] - round(expect, 4)) < 1e-9
    # shrink accounting exists and is distinct from PRE
    for k in ("shrink_ratio", "sections_downgraded", "sections_dropped", "root_downgraded",
              "serialized_before_shrink", "serialized_tokens", "target"):
        assert k in b
    s.close()


# fixed-resolution has no planner, so PRE is 0 (not 1.0) — no estimator was involved.
def test_pre_zero_for_fixed_resolution():
    s = _store()
    rr = read_symbol(s, "service.run_db", budget=512, resolution="slice")
    assert rr.budget["planned_vs_rendered_error"] == 0.0
    s.close()


# the advertised safety margin is actually applied to planning/materialization (2.3.1 P1).
def test_safety_margin_applied():
    s = _store()
    B = 400
    rr = read_symbol(s, "service.process", budget=B, safety_margin=0.10)
    assert rr.budget["safety_margin"] == 0.10
    assert rr.budget["target"] == int(B * 0.9)              # floor(B(1−m)), actually used
    assert rr.budget["serialized_tokens"] <= B             # B stays the absolute ceiling
    s.close()


# find_callers = reverse CALLS traversal, compact + handles
def test_find_callers():
    s = _store()
    callers = find_callers(s, "service.validate")
    names = {c["qualified_name"] for c in callers}
    assert "service.process" in names                  # process calls validate
    assert all(c["handle"].startswith("ctx://symbol/") for c in callers)
    s.close()


# context_search returns handles, never code dumps
def test_context_search_returns_handles_not_code():
    s = _store()
    hits = context_search(s, "process")
    assert hits and all(h["handle"].startswith("ctx://symbol/") for h in hits)
    assert all("text" not in h and "source" not in h for h in hits)   # no code
    assert any(h["qualified_name"] == "service.process" for h in hits)
    s.close()


# progressive expansion + bare-handle policy: a BARE ctx://symbol handle expands to a
# bounded SIGNATURE, never a full body; escalation to @implementation must be explicit
# (2.3.1 P1 — closes the search→handle→full-dump policy bypass).
def test_context_expand_bare_handle_is_signature():
    s = _store()
    sid = _sid(s, "service.run_db")
    bare = context_expand(s, f"ctx://symbol/{sid}")
    assert bare.found and "def run_db" in bare.text        # signature header still shown
    assert "signature" in bare.note                        # policy surfaced to the model
    full = context_expand(s, f"ctx://symbol/{sid}@implementation")
    assert full.found and len(full.text) >= len(bare.text)  # escalation is explicit + larger
    # an explicit @signature equals the bare default (same bounded view)
    sig = context_expand(s, f"ctx://symbol/{sid}@signature")
    assert sig.found and len(sig.text) == len(bare.text)
    # an UNKNOWN @level is NEVER an escalation. Since a symbol_id may itself contain '@',
    # an unrecognized suffix is treated as part of the id → resolves to nothing here, and in
    # no case to the full body. (The security-relevant property: junk ≠ implementation dump.)
    junk = context_expand(s, f"ctx://symbol/{sid}@qwerty")
    assert not junk.found
    assert junk.text != full.text
    # a '@' that is part of the symbol_id must not be parsed as a level suffix
    s2 = GraphStore(":memory:")
    _put(s2, "pkg::@scope/m.ts::foo", "tree_sitter", 1, 3, "function foo() {\n  a();\n}")
    at = context_expand(s2, "ctx://symbol/pkg::@scope/m.ts::foo")   # bare; '@scope' is in the id
    assert at.found and "signature" in at.note                     # resolved + bounded, not a dump
    s2.close()
    # unknown handle never silently empty
    bad = context_expand(s, "ctx://symbol/nope")
    assert not bad.found
    s.close()


def test_read_slice():
    s = _store()
    rr = read_slice(s, "service.run_db", budget=512)
    assert rr.ok and rr.sections[0]["level"] == "slice"
    s.close()


# ---- materialization honesty (2.3.1 P1): never call a bounded/partial body "implementation"

def _put(s, sid, parser, start, end, sample, original_bytes=None, kind="function"):
    ch = content_hash(sample + sid)
    s.put_symbol(CodeSymbol(symbol_id=sid, repo_id="t", language="python", kind=kind,
                            qualified_name=sid, path="m.py", start_line=start, end_line=end,
                            signature="f()", content_hash=ch, parser=parser,
                            resolution_quality=0.9, schema_version=SCHEMA_VERSION))
    ob = original_bytes if original_bytes is not None else len(sample.encode())
    s.put_blob(ch, ob, sample)
    return s.symbol_row(sid)


def test_truncated_source_is_flagged_not_passed_as_full():
    s = GraphStore(":memory:")
    # large symbol (byte_size > cap) that declares 40 lines but only 2 were stored -> truncated
    row = _put(s, "big", "python_ast", 1, 40, "def big():\n    a()", original_bytes=12000)
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "truncated"
    assert "truncated" in r.text                            # explicit marker, not a silent prefix
    assert r.provenance["source"]["complete"] is False
    assert r.provenance["source"]["span_lines"] == 40 and r.provenance["source"]["stored_lines"] == 2
    s.close()


# A COMPLETE small function whose multi-line secret redaction collapses (PEM → one token) must
# NOT be false-flagged as truncated (the final re-verification's usability finding).
def test_redaction_line_collapse_is_not_false_truncation():
    from contextruntime.redact import redact
    pem = ("-----BEGIN PRIVATE KEY-----\n" + "MIIB\n" * 3 + "-----END PRIVATE KEY-----")
    raw = f'def load_key():\n    K = """{pem}"""\n    return K'
    sample = redact(raw)                                    # collapses the PEM to fewer lines
    assert len(sample.splitlines()) < len(raw.splitlines()) # precondition: redaction dropped lines
    s = GraphStore(":memory:")
    ch = content_hash(raw)
    s.put_symbol(CodeSymbol(symbol_id="k", repo_id="t", language="python", kind="function",
                            qualified_name="k", path="m.py", start_line=1,
                            end_line=len(raw.splitlines()), signature="load_key()",
                            content_hash=ch, parser="python_ast", resolution_quality=0.95,
                            schema_version=SCHEMA_VERSION))
    s.put_blob(ch, len(raw.encode()), sample)               # full_stored: raw is well under the cap
    r = render_symbol(s, s.symbol_row("k"), "implementation")
    assert r.materialization_quality == "complete_ast"      # whole segment stored -> complete
    assert "truncated" not in r.text                        # no misleading marker
    s.close()


# CAS char-cap truncation must be caught even when the declared span is unknown (P1 hole).
def test_cap_hit_truncation_flagged_without_span():
    s = GraphStore(":memory:")
    row = _put(s, "capped", "python_ast", 1, None, "x = 1\n" * 1600, original_bytes=50000)
    assert len("x = 1\n" * 1600) >= 8000                    # sample hit the 8000-char cap
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "truncated"
    assert "truncated" in r.text
    s.close()


# A large original in the ambiguous byte zone (long-line truncation vs multibyte) must NOT be
# reported verified-complete — even when line count matches the span (the P1 re-refutation).
def test_unverifiable_extent_is_not_called_complete():
    s = GraphStore(":memory:")
    # byte_size in (CAP, CAP*4]: can't prove the whole raw source was stored -> unverified
    row = _put(s, "murky", "python_ast", 1, 2, "def murky():\n    s = 'x'", original_bytes=20000)
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "unverified"        # not "complete_ast"
    assert r.provenance["source"]["complete"] is False
    assert "unverified" in r.text                            # honest in-band marker
    s.close()


# The full raw source being provably stored (byte_size ≤ cap) is complete even if end_line is unknown.
def test_small_source_with_unknown_span_is_complete():
    s = GraphStore(":memory:")
    row = _put(s, "tiny", "python_ast", 10, None, "def tiny():\n    return 1")
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "complete_ast"      # whole source stored -> complete
    assert r.provenance["source"]["complete"] is True
    s.close()


# End-to-end through the REAL builder blob formula: a long secret-bearing line that redaction
# shrinks below the char cap must still be flagged (the skeptic's exact failing case).
def test_redaction_shrunk_truncation_flagged_end_to_end():
    from contextruntime.codegraph.adapters import PythonAstAdapter
    from contextruntime.model import content_hash as _ch
    from contextruntime.redact import redact
    s = GraphStore(":memory:")
    # a 2-line function whose 2nd line is a huge secret-bearing string (raw >> 8000 chars)
    src = 'def f():\n    s = "ghp_' + "A" * 40 + '" + "' + "x" * 20000 + '"\n'
    syms, _edges = PythonAstAdapter().parse("m.py", src, "m")
    fsym = next(x for x in syms if x.qualified_name == "m.f")
    # reproduce exactly what builder.index_path stores
    s.put_symbol(CodeSymbol(symbol_id="m::m.py::m.f", repo_id="m", language="python",
                            kind="function", qualified_name="m.f", path="m.py",
                            start_line=fsym.start_line, end_line=fsym.end_line,
                            signature=fsym.signature, content_hash=fsym.content_hash,
                            parser="python_ast", resolution_quality=0.95,
                            schema_version=SCHEMA_VERSION))
    s.put_blob(fsym.content_hash, len(fsym.source.encode()), redact(fsym.source[:8000]))
    r = render_symbol(s, s.symbol_row("m::m.py::m.f"), "implementation")
    assert r.materialization_quality != "complete_ast"      # NOT passed off as complete
    assert r.provenance["source"]["complete"] is False
    s.close()


def test_heuristic_declaration_only_is_flagged():
    s = GraphStore(":memory:")
    row = _put(s, "decl", "regex_heuristic", 5, 5, "function decl() {")
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "declaration_only_heuristic"
    assert "declaration only" in r.text                     # model is told the body is absent
    assert r.provenance["source"]["complete"] is False
    s.close()


def test_complete_ast_makes_no_truncation_claim():
    s = GraphStore(":memory:")
    row = _put(s, "small", "python_ast", 1, 2, "def small():\n    return 1")
    r = render_symbol(s, row, "implementation")
    assert r.materialization_quality == "complete_ast"
    assert "truncated" not in r.text and "declaration only" not in r.text
    assert r.provenance["source"]["complete"] is True
    s.close()
