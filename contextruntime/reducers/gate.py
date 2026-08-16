"""B1.0 — the prospective routing gate (Transparent Reduction Contract v0.1 §2).

The ONE decision the transparent reducer is allowed to make: *is this tool call a
prospectively-recognizable search/listing output* — knowable from the call itself,
before any prediction — or not? Everything else passes through unchanged. This is the
mechanical form of the project invariant "ContextRuntime does nothing when uncertain."

Routing reuses the FROZEN representation typing (`normalize.bash_effects`, hook_schema
0.4.1) — no new classifier is introduced, so the gate inherits the same evidence grade
as the observation corpus (contract §2).

Reducible representations (v0.1): `search`, `path_listing` only — the
`search_listing_reducible` bucket (FINDINGS §4, 29.7%). Deliberately NARROWER than the
Phase-1 reducer library:
  - native `Read`                         -> PASS THROUGH (edit-precondition risk, C10)
  - Bash `cat/head/tail/sed -n`  (file)   -> PASS THROUGH (a materialized file body)
  - Bash `git show REV:PATH`  (git_blob)  -> PASS THROUGH (historical blob)
  - Bash `... | wc -l`        (derived)   -> PASS THROUGH (already a tiny summary)
  - execution / edit / unknown            -> PASS THROUGH (not a read at all, or uncertain)
  - a multi-statement / conditional / partially-recognized Bash line -> PASS THROUGH
  - anything not Grep/Glob/Bash (WebFetch, Task, ...)                 -> PASS THROUGH

`tests`/`logs`/`git` reducers stay in the library but are NOT enabled by this gate in
v0.1 — they are a later, retrospective bucket, out of scope here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..normalize import bash_parse

# The only representations v0.1 acts on. Keep in sync with the contract; widening this
# set is a scope change, not a tweak.
REDUCIBLE_REPRESENTATIONS = frozenset({"search", "path_listing"})

# Native tools whose result IS, by construction, one of the reducible representations.
_NATIVE_SEARCH_TOOLS = frozenset({"Grep", "Glob"})


@dataclass(frozen=True)
class RouteDecision:
    reduce: bool
    reason: str
    tool: str = ""
    representation: Optional[str] = None
    reducer: str = "grep"          # the library reducer to apply when reduce is True

    @property
    def passthrough(self) -> bool:
        return not self.reduce


def _passthrough(tool: str, reason: str) -> RouteDecision:
    return RouteDecision(reduce=False, reason=reason, tool=tool)


def route(tool_name: Optional[str], tool_input: Optional[dict]) -> RouteDecision:
    """Decide whether a tool call's output is prospectively reducible. Pure + total:
    any unrecognized shape returns a pass-through decision, never raises."""
    tool = tool_name or "?"
    args = tool_input or {}

    # Native structured search tools: the result is a match/name listing by construction.
    if tool in _NATIVE_SEARCH_TOOLS:
        return RouteDecision(reduce=True, reason=f"native {tool} → search listing",
                             tool=tool, representation="search", reducer="grep")

    if tool != "Bash":
        # native Read, WebFetch/WebSearch, Task, MCP tools, Edit/Write, ... — not in
        # v0.1's prospective bucket. The default and only safe choice.
        return _passthrough(tool, f"{tool} is not a prospective search/listing call")

    command = str(args.get("command", "") or "")
    if not command.strip():
        return _passthrough(tool, "Bash with no command")

    parse = bash_parse(command)

    # A shell line the recognizer could not fully account for is uncertain by definition:
    # an unrecognized, conditional, or execution-bearing statement could be doing anything,
    # and a mixed line mingles a safe read with something we don't model. Pass through.
    if parse.has_execution:
        return _passthrough(tool, "Bash line runs code (execution) — not a pure read")
    if parse.has_unknown or parse.conditional:
        return _passthrough(tool, "Bash line only partially recognized — uncertain")
    if parse.coverage() != "fully":
        return _passthrough(tool, f"Bash coverage={parse.coverage()} — not a clean read set")

    reads = [e for e in parse.effects if e.kind == "read"]
    if not reads:
        return _passthrough(tool, "Bash line materializes no model-visible read")
    # EVERY read must be a reducible representation. One file/git_blob/derived operand and
    # the whole line passes through — we never partially reduce a mixed materialization.
    reps = {e.representation for e in reads}
    if not reps.issubset(REDUCIBLE_REPRESENTATIONS):
        offending = sorted(reps - REDUCIBLE_REPRESENTATIONS)
        return _passthrough(tool, f"Bash read representation(s) {offending} not prospectively reducible")
    # Any non-read effect (an edit alongside the read) makes the line unsafe to touch.
    if any(e.kind != "read" for e in parse.effects):
        return _passthrough(tool, "Bash line mixes a read with a non-read effect")

    rep = "path_listing" if reps == {"path_listing"} else "search"
    return RouteDecision(reduce=True, reason=f"Bash {rep} materialization",
                         tool=tool, representation=rep, reducer="grep")
