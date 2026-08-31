"""B8 harness — pure helpers only; the live chain/proxy path is not exercised in CI."""
import json
import os

import pytest

from corpus.b8_live_gated_ab import bite_from_transcript, chunk_prompt, gw_fires


def test_chunk_prompt_pins_worktree_and_problem():
    p = chunk_prompt({"problem": "floatformat is wrong"}, "/x/wt-django__django-16485")
    assert "/x/wt-django__django-16485" in p and "floatformat is wrong" in p and "DONE" in p


def test_bite_from_transcript_uses_b7_accounting(tmp_path):
    u = {"cache_read_input_tokens": 1000, "cache_creation_input_tokens": 100,
         "input_tokens": 10, "output_tokens": 20}
    rows = [{"type": "assistant", "requestId": "r1", "timestamp": "2026-08-28T00:00:00Z",
             "message": {"usage": u, "content": [{"type": "text", "text": "x"}]}}]
    tp = tmp_path / "s.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in rows))
    b = bite_from_transcript(str(tp))
    assert b["calls"] == 1
    assert b["bite"] == 0.1 * 1000 + 2.0 * 100 + 10 + 5.0 * 20      # 410
    assert b["sum_P"] == 1110


def test_gw_fires_aggregates_by_reason(tmp_path):
    log = tmp_path / "gw.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"fired": True, "fire_reason": "cold-start", "applied": 2, "thinking_stripped": 0},
        {"fired": False, "fire_reason": "hold", "applied": 0, "persistent_applied": 2,
         "thinking_stripped": 1},
        {"fired": True, "fire_reason": "ttl-gap", "applied": 3, "persistent_applied": 2,
         "thinking_stripped": 4},
        {"fallback_original": True},
    ]))
    g = gw_fires(str(log))
    assert g["fires"] == 2 and g["by_reason"] == {"cold-start": 1, "ttl-gap": 1}
    assert g["retired"] == 5 and g["persistent_applied"] == 2
    assert g["thinking_stripped"] == 5 and g["fallback_original"] == 1
    assert gw_fires(str(tmp_path / "none.jsonl")) is None


@pytest.mark.skipif(not os.path.exists(os.path.expanduser("~/.claude/projects")),
                    reason="needs local B6 transcripts")
def test_predict_band_matches_preregistration():
    """The frozen band in docs/b8-protocol.md derives from this call; drift means the protocol
    and the code disagree."""
    res = json.load(open("corpus/analysis/b6-live-results.json"))
    tp = res["tasks"]["django__django-16485"]["N0"].get("transcript")
    if not tp or not os.path.exists(tp):
        pytest.skip("B6 transcripts not on this machine")
    from corpus.b8_live_gated_ab import predict
    out = predict()
    assert out["gated"]["bite_delta_pct"] < -10 and out["gated"]["fires"] >= 3
    assert abs(out["gated"]["bite_delta_pct"] - (-17.06)) < 3.0
