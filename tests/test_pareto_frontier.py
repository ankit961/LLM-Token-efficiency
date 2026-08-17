"""Pareto domination logic — pure, no I/O."""
from corpus.pareto_frontier import mark_nondominated


def _r(b, f, x, y):
    return {"budget": b, "floor": f, "R_paired": x, "line_recall": y}


def test_marks_the_frontier_and_dominated_points():
    rows = mark_nondominated([
        _r(256, 125, 0.18, 0.56),   # frontier (max y)
        _r(128, 125, 0.43, 0.48),   # frontier (knee)
        _r(64, 125, 0.54, 0.10),    # frontier (max x)
        _r(256, 400, 0.12, 0.40),   # dominated by (256,125): lower x AND lower y
        _r(64, 400, 0.15, 0.00),    # dominated by (64,125): lower x, lower y
    ])
    nd = {(r["budget"], r["floor"]) for r in rows if r["non_dominated"]}
    assert nd == {(256, 125), (128, 125), (64, 125)}
    assert not next(r for r in rows if (r["budget"], r["floor"]) == (256, 400))["non_dominated"]


def test_none_axis_treated_as_zero():
    rows = mark_nondominated([_r(64, 400, 0.15, None), _r(256, 125, 0.18, 0.56)])
    # the None-recall point is dominated (lower on both once None→0)
    assert not rows[0]["non_dominated"] and rows[1]["non_dominated"]
