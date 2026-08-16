# Transparent Reduction Contract v0.1

**Status: design, zero Claude-quota cost to build against.** This is the next engineering step
after `docs/FINDINGS.md` (evidence) and `docs/semantic-admission-experiment-v1-CLOSED.md` (why
voluntary adoption is off the table). It supersedes nothing frozen — it's new scope.

## 0. Why transparent, not voluntary — and why this is buildable, not just a plan

Two facts, both load-bearing, both verified (not assumed):

1. **Voluntary doesn't work.** 0/11 real runs adopted the semantic MCP tools under two
   independently-designed advisory briefs, both confirmed delivered. See
   `semantic-admission-experiment-v1-CLOSED.md`. Any mechanism that depends on the agent *choosing*
   to call something is off the table.
2. **Transparent is mechanically real, not theoretical.** `PostToolUse`'s `updatedToolOutput` was
   live-verified on Claude Code 2.1.229 (`contextruntime/doctor.py`, commit `1aaff7c`): a controlled
   grep returning 200 real matching lines came back to the model as 15 lines + a `result://` handle
   — the existing Phase-1 `ContextReduce` reducer's own scheme, confirmed against a no-hook control
   showing the full 200 raw lines. **The core premise — the runtime rewrites what the agent sees,
   without asking — already works, on infrastructure this project already built.**

The invariant governing everything below, stated once and held everywhere:

> **ContextRuntime does nothing when uncertain.**

## 1. What the opportunity-ceiling analysis says to build first

`FINDINGS.md` §4 splits the 51.0% broad candidate mass into two buckets with a critical difference:

| bucket | share | knowable at... | mechanism |
|---|---:|---|---|
| `search_listing_reducible` | 29.7% (largest single bucket) | **runtime, before execution** — a grep/find/ls call is recognizable from the tool call itself | output compaction, zero prediction |
| `exploration_reducible` | 21.3% | **only in retrospect** — whether a read's path is later edited is unknown at read-time | needs a predictor/policy |

**v0.1 targets the prospective bucket only.** The retrospective bucket (native source `Read`) is
explicitly out of scope here — reducing it correctly needs an edit-precondition predictor, which
needs the retrospective corpus as training/eval ground truth (a separate, later design: "B2").
Conflating the two would violate the "does nothing when uncertain" invariant: we are NOT yet
certain a given native `Read` isn't an edit precondition.

## 2. Architecture

```
        Claude issues a native tool call (Grep / Bash / Read / ...)
                              │
                              ▼
                   PostToolUse fires (verified working)
                              │
                              ▼
                    ContextRuntime interception
                              │
              ┌───────────────┼────────────────┐
              │                                 │
      recognized prospective              everything else
      (search/listing/derived,             (native Read, edit-
       per the FROZEN representation        precondition-shaped,
       classifier -- normalize.py)          unrecognized, uncertain)
              │                                 │
              ▼                                 ▼
     graph-informed compaction              PASS THROUGH UNCHANGED
     (existing reducer + Code Graph            (the default; the ONLY
      symbol/edge lookup for ranking)           safe choice when uncertain)
              │
              ▼
   compact response + result:// expansion handle
   (lossless recovery -- already built, context_expand)
```

The classification of "is this call prospectively recognizable" reuses the **frozen**
`normalize.py` representation typing (`search` / `path_listing` / `derived` vs `file`) — the exact
same typing the observation layer and the opportunity-ceiling bucketing already use. **No new
classifier is built for this** — this is deliberate: that representation typing is already
validated across 50 real runs (Observation Corpus v2.1) and reusing it means v0.1's routing
decision has the same evidence grade as the corpus itself, not a fresh unvalidated heuristic.

## 3. Scope for v0.1 — exactly what gets built, nothing more

Per the priority ordering worked out in review (search/listing is prospectively actionable *today*;
a richer task-working-set graph is valuable but should not block shipping the actionable part):

| capability | today | v0.1 |
|---|---|---|
| Code Graph (symbols, `CALLS`/`IMPORTS`/`DEPENDS_ON`/`TESTED_BY`, confidence-scored) | ✅ built | reuse, no schema change |
| Residency Graph (`RESIDENT_IN`/`DUPLICATE_OF`/`MATERIALIZED_FROM`/`BROKE`) | ✅ built | reuse for dedup (§3.3) |
| Budgeted bundle planner, progressive resolution levels | ✅ built | reuse (already what `read_symbol` uses) |
| `context_search` → handles, not dumps | ✅ built | reuse |
| Phase-1 `ContextReduce` reducer library (grep/test/log/git) | ✅ built, **now verified live** | reuse as the base, extend (§3.1) |
| `PostToolUse` output-replacement wiring | ✅ verified this session | promote from `CR_OUTPUT_REPLACEMENT=1` manual flag to a real installer-managed setting (§3.4) |
| **Graph-informed grep/search compaction** | ❌ | **build now** — rank matched files/symbols via the Code Graph instead of a naive line-count summary (§3.1) |
| **Minimal Task Working-Set Graph (v0)** | ❌ | **build now**, narrowly scoped (§3.2) — NOT the full 4-evidence-source design |
| One-shot `context_compile(task, budget)` | ❌ | **design only in v0.1**, not implemented — needs the working-set graph proven first |
| Git co-change edges, runtime/failure edges | ❌ | **explicitly deferred** — real idea, not needed to capture the 29.7% |
| A different/heavier graph database | ❌ | **not needed** — SQLite/Graph-Lite stays |

### 3.1 Graph-informed search/listing compaction (the core v0.1 deliverable)

Today's `reduce_grep` (Phase-1, `contextruntime/reducers/library.py`) compacts by naive count +
file list. v0.1 upgrades this specifically: when a `Grep`/`Bash`-grep/`find` call's matched paths
overlap with **indexed symbols** in the Code Graph, rank the summary by graph relevance instead of
raw match count —

```
today:     "37 matches across 11 files. Top: parser.py (8), compiler.py (6), resolver.py (5)"
v0.1:      "37 matches across 11 files. Most graph-relevant to the current task: parser.py
            (contains 3 matched symbols with CALLS edges into files already read this session),
            resolver.py (TESTED_BY a currently-relevant test) ...
            result://<handle> for the full list"
```

This is the "29.7% and the graph architecture fit each other" connection made concrete: replace
count-based ranking with graph-based ranking, using infrastructure that already exists
(`GraphStore.code_edges_from`/`code_edges_to`/`search_symbols`, already used by `find_callers`/
`context_search`). Lossless recovery is unchanged — the `result://` handle already exists.

### 3.2 Task Working-Set Graph — v0, deliberately minimal

The full design discussed (prompt-mentions + failing-test + co-change + runtime-failure evidence,
a multiplicative scoring function over four graphs) is real and worth having — but v0.1 builds only
what's needed to make §3.1's ranking non-trivial, using data ALREADY captured, no new instrumentation:

- **`TOUCHED`**: paths this session has already read or edited (from the live `HookJournal` this
  session — already captured, zero new capture work).
- **`MENTIONS`**: symbols whose qualified name (or a suffix of it) appears in the task's own prompt
  text, resolved via the *already-built* forgiving bare-name resolver (`semanticfs._resolve`,
  fixed for headless steering in commit `76dbffa`).

That's it for v0.1 — two edge types, both derived from data already flowing through the system. The
scoring function is simplified accordingly (no `FAILS_IN`/co-change/runtime-failure terms, since
those data sources don't exist yet):

```
score(symbol) = graph_distance_from_touched_or_mentioned(symbol)   [primary rank]
              × confidence(edge)                                    [HARD > SOFT, never ambiguous]
              ÷ tokens(symbol, level)                                [budget efficiency]
```

This is intentionally close to the existing bundle planner's utility function (`R · C · W · D · F`
in `codegraph/bundle.py`) — v0.1 is an application of that planner to a working-set query, not a
new optimizer.

### 3.3 Deduplication via the Residency Graph

Before compacting a search/listing result, check `DUPLICATE_OF`/`MATERIALIZED_FROM` — if the exact
same content is already resident in context (a prior read/search returned it), the compacted
response can point at the existing residency rather than re-summarizing. This reuses the Residency
Graph exactly as built for Phase 0b/1; no schema change.

### 3.4 Installer wiring

`contextruntime install claude` currently wires `cr-hook` (observation) and `cr-policy`
(SessionStart brief) on `SessionStart`/`PreToolUse`/etc. v0.1 adds a `PostToolUse` entry for the
(now-verified) reducer hook, **advisory-equivalent by default**: ships with
`CR_REDUCE_MODE` unset (observe-only — reports what *would* be saved on stderr, per the existing
`hook.py` logic, changes nothing) and a `--enable-reduction` install flag required to actually flip
`CR_REDUCE_MODE=enforce`. This mirrors the semantic-directive arm's own posture (native fallback
never removed) — except here "fallback" isn't needed, because the runtime is the one deciding, and
it decides safely: falling back to pass-through is automatic whenever the recognized-prospective
condition (§2) isn't met.

## 4. Safety invariants (non-negotiable, testable)

1. **Never touch a read the frozen classifier would call `edit_precondition` or `verification`** —
   this is C10, already enforced project-wide; v0.1 only ever acts on `search`/`path_listing`/
   `derived` representations, which by construction (`normalize.py`) are never a specific file's
   pre-edit state.
2. **Never reduce below the budget floor without a `result://` recovery handle** — already a
   Phase-1 invariant (`invariants_ok` check in `reduce_result`), unchanged.
3. **Fail open on any uncertainty, error, or unrecognized shape** — pass through unchanged. This
   was already true; the reduction schedule is a graph-based summarizer, but the OFF switch remains
   identical to Phase-1's existing `_passthrough()`.
4. **Advisory-observe by default at install time** — reduction must be explicitly enabled, exactly
   like the semantic-directive brief's fail-open posture; the difference is enabling it doesn't
   depend on the agent cooperating.

## 5. Explicitly deferred (real ideas, not needed for v0.1, don't build yet)

- Git co-change edges, runtime/failure edges, a `FAILS_IN` task-graph relation — real, valuable,
  needs new instrumentation (co-change needs git history mining; failure edges need a live test
  harness) that v0.1 doesn't require.
- One-shot `context_compile(task, budget)` collapsing multiple MCP calls into one — needs the
  working-set graph proven useful first; premature to build the aggregate op before its parts work.
- Any reduction of native `Read` calls (the retrospective/oracle-ceiling bucket) — needs a
  predictor design (B2), out of scope here.
- A different graph database — SQLite/Graph-Lite has been sufficient at every scale tested so far.
- Arm C (`semantic_enforced`) — this document does not authorize it. v0.1's `PostToolUse` reduction
  is a different mechanism (output compaction on recognized-safe representations) from Arm C
  (hard admission denial), and stays purely advisory-by-default per §3.4.

## 6. Validation plan

Design + implementation + tests for §3.1–3.4 cost **zero Claude quota** — it's deterministic code
against the frozen classifier's existing representation typing and the existing graph stores, same
as `opportunity_ceiling.py`. The only point requiring a live Claude session is a **small,
purpose-built validation** (not another 50-run experiment): a handful of real grep/search-heavy
tasks, `CR_REDUCE_MODE=enforce`, checking (a) the reduction actually fires on real agent-issued
`Grep`/`Bash` calls in a live session (not just a synthetic test like §0's), (b) the agent's task
success is not degraded, (c) the `result://` expansion handle is actually usable if the agent asks
for more. This validation happens **after** implementation and review — not now.
