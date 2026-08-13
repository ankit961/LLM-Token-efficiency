"""Reducer contracts, the retention heuristic, and handles."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ingest import content_hash, est_tokens

# Below this, a result isn't worth reducing (envelope overhead would dominate).
MIN_REDUCE_TOKENS = 400

# Kinds we reduce aggressively vs. leave native.
REDUCE_KINDS = {"test_result", "log", "search_result", "tool_result"}
# source_slice (Read results) are NOT reduced by default: a read may be an
# edit precondition, and reducing it forces an under-context re-read next turn
# (design C10, §7 retention heuristic).
NEVER_REDUCE_KINDS = {"source_slice", "rule", "system_prompt", "tool_def",
                      "user_msg", "assistant_msg", "symbol"}


@dataclass
class ReducedOutput:
    reducer: str
    reduced_text: str
    handle: str                      # result://<hash> — resolves to the CAS blob
    raw_tokens: int
    reduced_tokens: int
    invariants_ok: bool = True
    preserved: list[str] = field(default_factory=list)   # critical lines kept
    note: str = ""

    @property
    def ratio(self) -> float:
        """reduced / raw (lower is better)."""
        return self.reduced_tokens / self.raw_tokens if self.raw_tokens else 1.0

    @property
    def saved_tokens(self) -> int:
        return max(0, self.raw_tokens - self.reduced_tokens)


def make_handle(raw_text: str) -> str:
    """A handle to the full payload; the runtime resolves it against the CAS."""
    return f"result://{content_hash(raw_text)}"


def should_reduce(kind: str, token_est: int, *, is_edit_target: bool = False,
                  ) -> tuple[bool, str]:
    """Retention decision (design C10 / §7).

    is_edit_target: set True when a read's path is later edited/written. Detecting
    this needs the exploration_read vs edit_precondition_read split (Phase 2); until
    then source reads are conservatively left native.
    """
    if kind in NEVER_REDUCE_KINDS:
        return False, f"{kind} left native (edit-precondition / low value)"
    if is_edit_target:
        return False, "likely edit target — reducing would force a re-read"
    if kind not in REDUCE_KINDS:
        return False, f"no reducer policy for kind={kind}"
    if token_est < MIN_REDUCE_TOKENS:
        return False, f"below MIN_REDUCE_TOKENS ({token_est} < {MIN_REDUCE_TOKENS})"
    return True, "reducible"


def tokens(text: str) -> int:
    return est_tokens(text)
