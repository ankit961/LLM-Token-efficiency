#!/usr/bin/env python3
"""B2.2/B2.3 — offline TRUE replay for file-read residency reduction. ZERO Claude quota.

`measured_file_reduction` mirrors what the live hook WOULD do for a file read, exactly — the file
analog of B1's `corpus.reduction_replay.measured_reduction`: apply the edit-safety gate, the size
floor, the exact-recovery predicate, the real `reduce_file`, and the beneficial guard; return
(reduced_tokens, eligible). A file the agent has already mutated, a small file, one whose recovery
would not be byte-exact, or one whose skeleton is not smaller is left UNCHANGED (reduced == raw).
"""
from __future__ import annotations

from contextruntime.reducers import livecas
from contextruntime.reducers.base import tokens as _tok
from contextruntime.reducers.fileeligibility import file_read_eligible
from contextruntime.reducers.library import FILE_BUDGET_TOKENS, FILE_REDUCE_FLOOR, reduce_file


def measured_file_reduction(raw: str, tool_name: str, tool_input: dict, *,
                            budget: int = FILE_BUDGET_TOKENS, floor: int = FILE_REDUCE_FLOOR,
                            edited_paths: frozenset = frozenset()):
    """(reduced_tokens, eligible) for one file read, mirroring the live hook's decision path."""
    raw_tok = _tok(raw)
    if not file_read_eligible(tool_name, tool_input or {}, edited_paths=edited_paths):
        return raw_tok, False                      # not a file read, or an active edit target → spare
    if raw_tok < floor:
        return raw_tok, False                      # too small to be worth a skeleton
    if not livecas.recovery_is_exact(raw):         # redacted / over byte cap ⇒ hook would pass through
        return raw_tok, False
    red = reduce_file(raw, tool_input or {}, budget_tokens=budget)
    if not red.invariants_ok or red.reduced_tokens >= raw_tok:
        return raw_tok, False                      # invariant fail OR not beneficial → pass through
    return red.reduced_tokens, True
