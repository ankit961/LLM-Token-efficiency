# ContextRuntime — observed findings (as of 2026-08-16)

This is an evidence ledger, not a pitch: what has actually been measured, on what sample, with
what evidence grade — and what remains open. Each section links to the primary artifact/doc it
summarizes; none of the numbers here are re-derived, only consolidated.

## 1. What ContextRuntime observes reliably (Observation Corpus v2.1)

50 controlled task runs (django/django, SWE-bench-style, 5 fix-shape strata × 10), collected under
the frozen observation runtime `obs-runtime-3a-v2.1` (hook_schema 0.4.1). Full methodology:
`docs/corpus-protocol.md`, `docs/corpus-plan-v2.1.md`.

- **49/50 runs engaged** (real reads + edits; only 1 task drew a single read — a defensible
  agent no-op on a hard task, see §3).
- **Token attribution is strong and honest**: 91.3% of classified reads (538/589) are
  `fully_attributed_text` — real, mechanically-measured token weights, not estimates. The
  remaining 8.7% is ordinary composite/multipath ambiguity (still counted, just not attributable
  to one path); **zero** reads fell into an unmeasured/multimodal bucket.
- **Causal attribution is genuine, not proximity-guessed**: `seq_fallback = 0` in every single run
  — every edit-precondition link was resolved by real read→edit linkage. 76.6% of those links are
  at causal distance 1 (the read immediately precedes its edit).
- **Read-role classification exercised at scale**: `unknown` 67.2% (the large majority of this is
  the *intended* v0.4.1 search→UNKNOWN routing — grep/find results correctly refuse a confident
  exploration/edit-precondition label, not noise), `edit_precondition` 18.8%, `exploration` 12.2%,
  `verification` 1.7%. `config_required` was never exercised (0 occurrences across the corpus).
- **Admission is honest about its own limits**: 47/50 runs are `canonical_admissible`. The 3
  exceptions fail exactly one check (`pre_equals_batch`) — adjudicated (7-agent workflow + direct
  verification of the deliveries ledger) as expected Claude Code behavior for non-materializing
  tool calls, not a capture defect. They are retained and counted, not dropped.
- **Two honest scope gaps, stated plainly**: `bash_coverage_clean = FALSE` on all 50 runs (shell-
  statement recognition is real but not validated as complete); `config_required` is entirely
  unexercised. The corpus is fit for what it measured — file-read classification and text token
  attribution — and does not vouch for bash coverage.

**Evidence grade**: A (mechanically measured, hook-level, reproducible from raw journals + hashes).

## 2. Native task-success baseline (Arm A)

The 50 Observation Corpus v2.1 patches, graded against the official SWE-bench_Verified harness
(GitHub Actions, `swebench==4.1.0`, prebuilt images — run `31939295114`, all jobs green).

**S_A = 40/50 = 80% resolved.** Per fix-shape stratum:

| stratum | resolved | rate |
|---|---|---|
| fs1 — one-line | 10/10 | 100% |
| fs2 — small | 8/10 | 80% |
| fs3 — medium | 10/10 | 100% |
| fs4 — large | 7/10 | 70% |
| fs5 — multi-file | 5/10 | 50% |

A credible, complexity-correlated baseline — success declines roughly monotonically with fix size/
file count. One caught-and-fixed measurement bug along the way: the empty-patch task (a defensible
no-op on a task whose literal ask was already satisfied at base, per the issue text) was initially
mislabeled a grading infrastructure error; fixed to score correctly as unresolved, so `S_A` is not
inflated by excluding a real failure. Full detail: `corpus/arm-a/grading-summary-v1.json`.

**Evidence grade**: A (official harness, FAIL_TO_PASS/PASS_TO_PASS, reproducible run ID).

## 3. The semantic mechanism — validated sound, but doesn't self-activate

`docs/semantic-admission-experiment-v1-CLOSED.md` is the full record; summary here.

**What works, confirmed by direct reproduction (not inferred):**
- The SemanticFS MCP server starts reliably and `read_symbol` resolves natural (bare)
  class/function/method names against a real, large codebase (django: 43,241 navigable symbols).
- Two real defects were found and fixed along the way, each verified broken-then-fixed on the
  exact case that exposed it:
  - A cross-repo MCP startup failure (the server subprocess couldn't import `contextruntime` when
    spawned in a different repo's worktree) — fixed via explicit `PYTHONPATH`.
  - A bundle-planner budget blowout (99% of budget spent on an uncapped ambiguity-candidate dump
    for a commonly-named symbol, leaving only 20 tokens of actual function body) — fixed by
    capping shown candidates at 5 with an honest "+N more."
- The advisory steering channel (`--append-system-prompt`) is **confirmed delivered** to the model
  via a per-run log (`mcp_enabled` / `brief_included` / `brief_chars` / `brief_version`), not
  assumed.

**What doesn't work: spontaneous adoption under advisory steering.**
Across 11 real runs (6 distinct SWE-bench tasks) under **two independently-designed** advisory
briefs — v1 ("call `read_symbol` first"), v2 ("locate via `context_search`, then examine via
`read_symbol`") — **adoption was 0/11.** Both briefs were confirmed delivered; the mechanism
itself is confirmed working when explicitly invoked (a forced call resolves correctly and returns
a budget-correct bundle). The agent simply never chose to use it on its own, on this task type.

**Interpretation**: a system-prompt-level policy nudge does not compete with a concrete,
task-embedded file or traceback reference already in the prompt — which is exactly what most
SWE-bench issues provide. This held regardless of which specific tool the nudge emphasized.

**Evidence grade**: A for the null result itself (11/11 zero, both brief versions log-confirmed
delivered); the *interpretation* (why) is reasoned from the evidence, not independently tested.

## 4. The opportunity ceiling — how much is there to gain, if adoption weren't voluntary

§3 established that asking the agent to opt in doesn't work. That reframes the question: if a
runtime could safely substitute or reduce reads on the agent's behalf — never asking, never
depending on cooperation — how much of the corpus's actual read-token volume is even a plausible
candidate? This is answered entirely offline, at zero cost, over the 50 already-classified
Observation Corpus v2.1 journals (`corpus/opportunity_ceiling.py`, output committed at
`corpus/analysis/opportunity-ceiling-v1.json`). It reuses the frozen classifier exactly as the
label-report does; it adds no new classification, only a bucketing of the existing labels/reasons
by reduction candidacy, deliberately as conservative as the classifier itself — an `UNKNOWN` read
is never optimistically counted as reducible just to raise the number.

Of **246,686 fully-measured read tokens** across all 50 runs (cross-checked exactly against §1's
91.3%/589-read figures — 538 fully-attributed + 29 `ambiguous_composite` + 22 `ambiguous_multipath`
= 589):

| bucket | tokens | share | meaning |
|---|---:|---:|---|
| `required` (edit precondition) | 70,726 | 28.7% | must remain exact, never a candidate |
| `search_listing_reducible` | 73,360 | 29.7% | grep/find/listing output — a *different* mechanism (output compaction, i.e. Phase-1 `ContextReduce`), not semantic substitution |
| `exploration_reducible` | 52,535 | 21.3% | confidently no future mutation — the classic semantic-substitution candidate |
| `unresolved_other` | 44,610 | 18.1% | classifier is correctly uncertain — not counted as reducible |
| `verification` | 5,455 | 2.2% | post-edit re-check, reported separately |

```
C_safe  = exploration_reducible / all                         = 52,535 / 246,686 = 21.3%
C_upper = (exploration_reducible + search_listing_reducible) / all = 125,895 / 246,686 = 51.0%
```

**Read as: even before touching the adoption problem, 21–51% of context materialized in these
tasks is a plausible reduction candidate under runtime-mediated control** — the conservative
number counts only confident-exploration reads; the optimistic number also counts search/listing
output, which is a different and already-partially-built mechanism (the Phase-1 `ContextReduce`
reducer library), not a new one. Per-stratum, the ceiling doesn't collapse anywhere: `C_safe` stays
in a 15–26% band across all 5 fix-shape strata, and `C_upper` in a 39–62% band — the opportunity
isn't concentrated in one easy stratum and absent elsewhere.

**Evidence grade**: A (pure arithmetic over already-classified, already-verified data; the
bucketing logic is unit-tested and the sanity totals reconcile exactly with §1's independently
reported figures).

## 5. What remains open

- **Arm C (`semantic_enforced`)** — reserved, unimplemented, unstarted. §3's finding is relevant
  evidence for a future enforcement discussion but does not itself authorize building it; that
  needs an explicit, separate decision.
- **Whether *any* advisory design could move adoption** — two designs were tried, not exhaustively
  all possible designs. The evidence supports "advisory alone is a weak lever here," not "no
  advisory design could ever work."
- **bash coverage completeness** and **config_required exercise** — both flagged, neither closed.
- **Cross-agent generalization** (Codex, etc.) — untested; everything above is Claude-specific.

## 6. What's durable regardless of any single experiment's outcome

The mechanism fixes (§3), the observation instrumentation (§1), the grading pipeline (§2), and the
opportunity-ceiling analysis (§4) are now permanent, reusable infrastructure — they don't depend on
the adoption finding and remain correct and available for direct/explicit use.
