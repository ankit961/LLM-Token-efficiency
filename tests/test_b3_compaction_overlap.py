"""B3.2 native-/compact overlap model — boundary detection, deferral, redundancy accounting."""
from corpus.b3_compaction_overlap import (deferral_model, detect_boundaries, redundancy_model,
                                          _retired_cumulative, _retire_turns)


def _objs():
    # a.py read@1 superseded by read@5; b.py read@3 tail; c.py read@2 tail
    return [{"turn": 1, "key": "path:a.py", "size": 400},
            {"turn": 5, "key": "path:a.py", "size": 400},
            {"turn": 3, "key": "path:b.py", "size": 600},
            {"turn": 2, "key": "path:c.py", "size": 300}]


def test_detect_boundaries_flags_large_drops():
    cr = {1: 60_000, 2: 120_000, 3: 30_000, 4: 130_000}    # drop at turn 3 (120k→30k)
    assert detect_boundaries(cr, 4) == [3]
    assert detect_boundaries({1: 40_000, 2: 10_000}, 2) == []   # prior level below floor ⇒ ignored


def test_retired_cumulative_is_monotonic():
    objs = _objs()
    retire = _retire_turns(objs, 20, lag=5)
    cum = _retired_cumulative(objs, retire, 20)
    assert all(cum[t] <= cum[t + 1] for t in range(1, 20))
    assert cum[20] > 0                                     # something retired by the end


def test_deferral_reduces_peak_and_defers_crossing():
    objs = _objs()
    # resident dominated by objects; B3 retirement lowers later-turn residency
    cr = {t: 1000 + 300 * t for t in range(1, 21)}         # grows to ~7000 at t=20
    d = deferral_model(objs, cr, 20, thetas=(3000,), lag=1)
    assert d["peak_b3"] <= d["peak_native"]                # B3 never raises the peak
    assert d["peak_reduction_pct"] >= 0
    row = d["by_theta"][0]
    if row["would_compact"]:                               # if native crosses θ, B3 crosses no earlier
        assert row["K_b3"] is None or row["K_b3"] >= row["K_native"]


def test_redundancy_unique_fraction_below_one_with_boundaries():
    objs = _objs()
    # a reset at turn 8 evicts everything created before it ⇒ B3 gets no credit past turn 8
    cr = {t: 60_000 for t in range(1, 8)}
    cr[8] = 10_000                                         # boundary at 8
    for t in range(9, 21):
        cr[t] = 60_000
    r = redundancy_model(objs, cr, 20, lag=1)
    assert r["n_boundaries"] == 1
    assert 0 < r["unique_fraction"] < 1                    # native captures part of B3's standalone saving
    assert r["unique_tokturns"] < r["standalone_tokturns"]
