"""ContextPolicy — the semantic-first, fail-open decision layer.

Load-bearing properties: fail-open by default, C10 (edit-precondition reads are never steered or
reduced), semantic-first only when the bundle is genuinely no larger, and enforcement gated behind
a doctor-confirmed capability (so today it is advisory no matter what mode is asked).
"""
from contextruntime import policy as P
from contextruntime.policy import ToolCall, decide, decide_safe


CONFIRMED = {"capabilities": {"pre_tool_use_hook": "yes", "edit_read_tracker_satisfied_by_mcp": "yes"}}
UNCONFIRMED = {"capabilities": {"pre_tool_use_hook": "?", "edit_read_tracker_satisfied_by_mcp": "?"}}


# --------------------------------------------------------------------------- fail-open
def test_unknown_kind_fails_open():
    d = decide(ToolCall(kind="unknown", target="x"))
    assert d.action == P.PASS_THROUGH and d.fail_open


def test_empty_call_fails_open():
    d = decide(ToolCall())
    assert d.action == P.PASS_THROUGH and d.fail_open


def test_decide_safe_never_raises_and_fails_open():
    class Bomb:  # not a ToolCall — accessing .kind explodes inside the decision logic
        @property
        def kind(self):
            raise RuntimeError("boom")
    d = decide_safe(Bomb())
    assert d.action == P.PASS_THROUGH and d.fail_open and d.reason.startswith("policy_error")


def test_execution_is_not_steerable():
    d = decide(ToolCall(kind="execution", target="pytest"))
    assert d.action == P.PASS_THROUGH


# --------------------------------------------------------------------------- C10 protection
def test_edit_precondition_read_is_never_steered_even_with_semantic_available():
    d = decide(ToolCall(kind="read", target="m.f", is_edit_target=True, is_source=True,
                        semantic_available=True, semantic_bundle_tokens=10, token_est=1000))
    assert d.action == P.PASS_THROUGH and d.reason == "edit_precondition_protected"


def test_edit_kind_is_never_steered():
    d = decide(ToolCall(kind="edit", target="m.f", is_source=True, semantic_available=True))
    assert d.action == P.PASS_THROUGH and d.reason == "edit_precondition_protected"


# --------------------------------------------------------------------------- semantic-first
def test_source_read_with_smaller_bundle_recommends_semantic():
    d = decide(ToolCall(kind="read", target="pkg.mod.func", is_source=True,
                        semantic_available=True, semantic_bundle_tokens=120, token_est=800))
    assert d.action == P.RECOMMEND_SEMANTIC and d.confidence == "high"
    assert d.evidence["saved_est"] == 680 and not d.fail_open


def test_semantic_not_recommended_when_bundle_is_larger():
    d = decide(ToolCall(kind="read", target="pkg.mod.func", is_source=True,
                        semantic_available=True, semantic_bundle_tokens=2000, token_est=300))
    assert d.action == P.PASS_THROUGH and d.reason == "semantic_bundle_not_smaller"


def test_source_read_without_coverage_passes_through():
    d = decide(ToolCall(kind="read", target="pkg.mod", is_source=True, semantic_available=False,
                        token_est=500))
    assert d.action == P.PASS_THROUGH and d.reason == "no_semantic_equivalent"


def test_unknown_bundle_cost_is_medium_confidence_but_still_recommends():
    d = decide(ToolCall(kind="read", target="s", is_source=True, semantic_available=True,
                        semantic_bundle_tokens=None, token_est=400))
    assert d.action == P.RECOMMEND_SEMANTIC and d.confidence == "medium"


def test_search_over_indexed_code_prefers_context_search():
    d = decide(ToolCall(kind="search", target="save", semantic_available=True))
    assert d.action == P.RECOMMEND_SEMANTIC and d.evidence["tool"] == "context_search"


def test_non_source_reducible_output_defers_to_reducer():
    d = decide(ToolCall(kind="read", target="pytest.log", is_source=False, reducible_output=True,
                        token_est=5000))
    assert d.action == P.REDUCE_OUTPUT and d.mode_effective == P.ADVISORY


# --------------------------------------------------------------------------- enforcement gating
def test_enforce_stays_advisory_without_confirmed_capability():
    d = decide(ToolCall(kind="read", target="s", is_source=True, semantic_available=True,
                        semantic_bundle_tokens=50, token_est=500),
               mode=P.ENFORCE, capability=UNCONFIRMED)
    assert d.action == P.RECOMMEND_SEMANTIC and d.mode_effective == P.ADVISORY and not d.enforced


def test_enforce_becomes_deny_nudge_only_with_confirmed_capability():
    d = decide(ToolCall(kind="read", target="s", is_source=True, semantic_available=True,
                        semantic_bundle_tokens=50, token_est=500),
               mode=P.ENFORCE, capability=CONFIRMED)
    assert d.action == P.DENY_NUDGE and d.mode_effective == P.ENFORCE and d.enforced


def test_enforce_never_denies_an_edit_precondition_read():
    d = decide(ToolCall(kind="read", target="s", is_edit_target=True, is_source=True,
                        semantic_available=True, semantic_bundle_tokens=10, token_est=900),
               mode=P.ENFORCE, capability=CONFIRMED)
    assert d.action == P.PASS_THROUGH  # C10 beats enforcement


# --------------------------------------------------------------------------- advice rendering
def test_advise_is_none_on_passthrough_and_string_on_intervention():
    assert P.advise(decide(ToolCall(kind="unknown"))) is None
    msg = P.advise(decide(ToolCall(kind="read", target="pkg.f", is_source=True,
                                   semantic_available=True, semantic_bundle_tokens=100, token_est=900)))
    assert msg and "semantic read surface" in msg and "pkg.f" in msg
