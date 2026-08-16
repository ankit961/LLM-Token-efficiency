# Semantic Admission Experiment v1 — CLOSED (2026-08-16)

**Status: closed by user decision after a validated null result.** This document is the closing
synthesis. It supersedes the *plan of action* in `semantic-admission-experiment-v1.md` and
`semantic-admission-experiment-v1-steering-v2.md` (both remain the historical record of what was
attempted and why) without editing either — consistent with the freeze condition in the original
protocol.

## The question

> Does ContextRuntime save substantial context without making Claude worse?

## What got answered, and what didn't

**Arm A (native) — answered.** `S_A = 40/50 = 80%` resolved on the frozen Observation Corpus v2.1
patches, graded via the official SWE-bench harness (GitHub Actions run `31939295114`). Per
fix-shape stratum: fs1 100%, fs2 80%, fs3 100%, fs4 70%, fs5 (multi-file) 50% — a credible,
complexity-correlated baseline. See `corpus/arm-a/grading-summary-v1.json`.

**Arm B (semantic_directive) — did not reach a task-success comparison, because adoption never
happened.** The paired A/B analysis in the original protocol assumed the agent would actually use
the semantic surface at some non-trivial rate. It did not: **0 semantic reads across 11 real runs,
under two independently-designed advisory briefs.** There is nothing to pair against — Arm B's
patches are, behaviorally, native-Claude patches with an unused tool sitting in the MCP registry.
11 runs touched 6 of the 50 locked tasks (run-02..07, several tried under both brief designs); the
other 44 were never attempted under any Arm B condition. Running them would not change this
conclusion — 11 runs under two distinct steering designs is already a robust null result, and
grading 44 more native-equivalent patches would not answer a different question than Arm A
already did.

## What was actually validated (this is the real result)

Two real, independent defects in the semantic mechanism were found and fixed along the way — not
guessed, each verified with direct reproduction before and after:

1. **MCP cross-repo startup failure** (`0370b85`): the MCP server subprocess couldn't import
   `contextruntime` when spawned with `cwd` set to the target repo's worktree (not this package's
   own root). Fixed via an explicit `PYTHONPATH` in the generated MCP config.
2. **Ambiguity-hint budget blowout** (`30feb00`): a real, correctly-resolved django symbol with
   several commonly-named references (`get`/`set`/`deconstruct`/`compile`) returned 99% overhead
   and `budget_insufficient=true` — an uncapped candidate-list dump was consuming nearly the whole
   budget instead of the requested symbol's own body. Fixed with a cap (5 shown, honest "+N more").

With both fixed, the mechanism is **confirmed sound**: the MCP server starts reliably, `read_symbol`
resolves natural (bare) names, and returns budget-correct, useful bundles. The steering channel
(`--append-system-prompt`) is **confirmed delivered** — not inferred — via a new per-run log
(`85b4714`) that records `mcp_enabled` / `brief_included` / `brief_chars` / `brief_version` for
every run, so this question never has to be re-litigated by live reproduction again.

**Two brief designs were tried, both confirmed delivered, both 0/adoption:**

| brief | imperative | runs | adoption |
|---|---|---|---|
| v1 | "call `read_symbol(name)` FIRST" | 6 | 0/6 |
| v2 (`v2-locate-then-examine`) | `context_search` to locate, `read_symbol` to examine | 5 | 0/5 |

v2's diagnosis was well-grounded (run-07 did 16 native reads before finishing — consistent with a
genuine locate/explore phase that `read_symbol`, which needs an exact name upfront, cannot serve;
`context_search` can). It still didn't move behavior. The honest conclusion: **a system-prompt-
level advisory nudge does not compete with a concrete, task-embedded file/traceback reference** —
regardless of which specific tool is emphasized. This is consistent with, and now much better
evidenced than, the much earlier single-task observation in this project's history.

## Conclusion

The mechanism (Graph-Lite → SemanticFS → ContextPolicy → MCP) works and is verified correct. Making
it *change agent behavior* through advisory means alone does not work, at least not for SWE-bench-
style debugging tasks with a concrete file/traceback reference already in the prompt. This is not a
partial or ambiguous result — it is an 0/11 outcome across two genuinely different steering designs,
both confirmed delivered to the model.

**Decision (user, 2026-08-16): accept this finding. Reframe the product's demonstrated value around
observability/measurement — the part that is proven to work reliably (accurate token attribution,
read/edit classification, per-session dashboards) — rather than continuing to chase adoption via
advisory steering.**

## What this does NOT close

- Arm C (`semantic_enforced`) remains reserved, unimplemented, and unstarted. This finding is
  relevant evidence for a future Arm C discussion (advisory alone doesn't move behavior — an
  enforcement mechanism might) but that is an explicit, separate decision, not implied by closing
  this experiment.
- Observation Corpus v2.1 is untouched and remains valid for everything it was built to measure.
- The SemanticFS mechanism itself (now with both bugs fixed) remains available and correct for
  direct/explicit use (CLI, or an agent choosing to call it) — this closure is about *spontaneous
  adoption under advisory steering*, not about the tool's own quality or availability.

## Artifacts

- Arm A patches + grading: `corpus/arm-a/` (committed, graded, closed)
- Arm B exploratory runs (11 total, not a confirmatory sample, kept for provenance):
  `$SCRATCHPAD/arm-b/runs-v1-brief-exploratory-20260816/` (6, v1 brief) and
  `$SCRATCHPAD/arm-b/runs/` (5, v2 brief) — both local/uncommitted (contain journals/telemetry).
- The two mechanism fixes and the steering redesign are permanent, product-quality improvements to
  `contextruntime/` regardless of this experiment's outcome — they ship either way.
