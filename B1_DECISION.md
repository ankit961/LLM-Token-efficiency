# B1_DECISION — transparent search-output reduction (FROZEN)

> **Correction (2026-10-05, post-hoc audit):** five statements below do not match the code or the
> committed artifacts. The original text is kept as written.
>
> 1. **The freeze was never applied to code.** This doc freezes `(256,125)` with graph ranking OFF.
>    The shipped code still defaults to floor `MIN_REDUCE_TOKENS = 400`
>    (`contextruntime/reducers/hook.py:40`, `contextruntime/reducers/base.py:9`) with budget
>    `SEARCH_BUDGET_TOKENS = 256`, and `contextruntime/install.py` (`default_reducer_cmd`) passes
>    `CR_GRAPH_DB` / `CR_REPO_ID` / `CR_JOURNAL_DB` to the reducer hook without setting
>    `CR_GRAPH_MODE=off` or `CR_REDUCE_FLOOR`. An `--enable-reduction` install therefore runs
>    `(256,400)` with graph ranking eligible, not the policy below, and no test pins the frozen
>    defaults. Bringing the code in line with this decision is a separate, later step; this note only
>    records the gap.
> 2. **"12.1% of search-output tokens" is not the Step-4 number.** Step-4's 12.1% is `R_direct`, a
>    share of ALL read tokens. Step 6's 12.1% is `R_paired`, a share of search-output tokens only. The
>    like-for-like Step-4 figure is `R_search_micro(256,400) = 40.8%`
>    (`corpus/analysis/reduction-replay-v1.2.json`), so the cap model over-predicted the measured value
>    by ~3.4×; the two 12.1% figures agree only by a coincidence of units. Open caveat (unverified):
>    the Step-6 replay read transcripts from client 2.1.229, which stores the reduced tool output, and
>    the pilot's 14 live reductions all fired at floor 400 while the replay sees only 9/184 events
>    ≥400 tok. The replay may therefore be biased low; the audit models `R_paired(256,400)` at
>    ~36–38% (close to the 40.8% estimate), but this cannot be re-run here because the pilot
>    transcripts are not on this machine.
>    The `(256,125)` paired 0.183 exists only in Step-6.1 prose (no committed JSON) and carries the
>    same caveat, and its line-recall "dominance" compares different needed-path sets (2/5 vs 9/16),
>    not paired events.
> 3. **Step-7 statistics.** r(T_total, turns) = 0.988, so r² = 0.976. "~71k tokens/turn" is
>    ΣT_total/Σturns; the OLS slope is ~110k tokens per extra turn. The "+10%/+20%" ΔT_total are
>    means of per-task ratios; pooled over completed runs they are +6.4% / +20.8% (turns
>    +4.9% / +15.8%).
> 4. **"Zero repeated searches" holds only for exact repeats.** `exact_search_repeat_count` is 0 in
>    all 60 runs, but the broader metric the Step-5 protocol designated, `repeated_scope_count`, rose
>    from 55 (A) to 95 (D1) to 110 (D2), summed over each arm's 20 runs (D2 vs A: task-stratified
>    permutation p ≈ 0.005), and was not reported. "No recovery penalty" is therefore not established.
> 5. **Task success was never graded.** `task_resolved` is null for all 60 Step-7 runs.
>
> Sources: `corpus/analysis/step7-live-results.json`, `corpus/analysis/reduction-replay-v1.2.json`,
> and the code paths named above.

**Status: research phase complete. This freezes the B1 policy and ends experiment-methodology
changes.** ContextRuntime B1 is now a product-engineering project. Evidence: `docs/step4..step7`,
`docs/step6.1-evidence-retention-findings.md`, `docs/step7-live-findings.md`.

## Decision

- **Default policy: `(budget=256, floor=125)`.** [corrected 2026-10-05: never applied in code; the
  shipped default is still `(256,400)`, see the correction at top] The conservative Pareto winner — it changes *only
  the floor* from shipped `(256,400)`, dominates it on both offline axes (paired reduction
  0.121 → 0.183; most inline evidence of any budget, ~7.4 retained match-lines/fired event), and
  showed **no recovery penalty and no wall/completion regression** live.
- **Graph ranking: OFF on the B1 search-reduction path.** [corrected 2026-10-05: the installer leaves
  graph ranking eligible, see the correction at top] Deprioritized, not deleted — Graph-Lite
  stays in ContextRuntime for SemanticFS / symbol bundles / dependency + task-state reasoning.
- **Both `CR_REDUCE_BUDGET` and `CR_REDUCE_FLOOR` remain runtime-configurable** (defaults above);
  do not hard-code. This is the seam the future adaptive policy `B(x)` plugs into.

## Confidence and the honest value statement

**What is proven:** transparent interception works; exact `result://` recovery works; the reducer
compresses **12.1%** [corrected 2026-10-05: a search-only `R_paired`, possibly biased low, and not the
same quantity as Step-4's 12.1%; see top] of search-output tokens at the shipped setting (Step 6, measured on 184 real
outputs) and up to ~54% aggressively; it is **safe** — every safety invariant holds, and across 60
live sessions there were **zero** `result://` expansions, **zero** repeated searches [corrected
2026-10-05: zero *exact* repeats; `repeated_scope_count` rose 55 → 95 → 110], no rise in
native re-reads under D1, and wall time within ~4% of native.

**What is NOT true — and cannot be, via search reduction alone:** a whole-session token saving.
Live `T_total` is **98.8% correlated with turn count** [corrected 2026-10-05: r = 0.988, r² = 0.976]
(~71k tokens/turn [corrected 2026-10-05: ΣT/Σturns; the OLS slope is ~110k] — the cache-dominated
prefix re-read every turn), while the reducer's direct saving is **~935 tok/session = 0.028% of
`T_total`**. The `+10%/+20%` [corrected 2026-10-05: means of per-task ratios; pooled +6.4%/+20.8%]
ΔT_total seen for D1/D2 is trajectory-length variance, not reduction.
So B1 does **not** clear the "consistent whole-session context-burden reduction" gate, and the
magnitude argument says it never will on its own: **search outputs are a rounding error against the
prefix.** Ship B1 as *safe, transparent per-read compaction* (which also declutters the
model-visible payload and compounds toward ~5–10% on long, grep-heavy sessions — n=1 task, not
established), **not** as a whole-session cost optimizer.

**Caveats:** 4 django tasks, sonnet, 5 reps; task success graded later (patches saved) [corrected
2026-10-05: never graded; `task_resolved` is null for all 60 runs]; the
long-session upside is a single-task hint.

## Strategic redirect (the input to whatever comes after B1)

The live measurement confirms the project's founding thesis: **the master lever is the re-read
prefix, not tool outputs.** Whole-session cost = turns × cached-prefix size. The token-cost mission
moves to reducing what gets cached and re-read every turn — **file reads, conversation history, tool
definitions** — reusing exactly the B1 infrastructure (prospective gate, transparent PostToolUse
replacement, confirmed CAS + exact recovery, fail-open version gating).

## Safety invariants (frozen — carry into production unchanged)

1. **Fail-open everywhere.** Uncertain / unsafe / unrecognized / non-beneficial ⇒ pass the native
   output through untouched. ContextRuntime does nothing when uncertain.
2. **Prospective eligibility gate only.** Reduce only outputs recognizable *before* running as
   search/listing (`gate.REDUCIBLE_REPRESENTATIONS`); never file/git_blob/derived/execution/unknown.
3. **Replace only when persisted AND exact.** The raw is stored in the live CAS and read back;
   replacement happens only if recovery is byte-exact (`recovery_is_exact`: not truncated, redaction
   a no-op). A dangling `result://` handle must never be emitted.
4. **Beneficial-only.** Never replace unless `T_replacement < T_native` (diagnostics + envelope can
   exceed a small raw output).
5. **Diagnostics + `path:lineno` preserved** within budget; a per-file rollup preserves result shape
   when match lines are dropped; a `result://` recovery handle is always appended.
6. **Runtime fail-safe version gate.** Enforce output replacement only when the *live* client version
   is confirmed (`doctor.live_client_version()` probe); an undetermined version ⇒ do NOT enforce
   (never trust a stale baked value).

## Supported Claude Code versions

- **Confirmed for output replacement:** `2.1.229` (`doctor.CONFIRMED_OUTPUT_REPLACEMENT_VERSIONS`).
- **Any other / undetermined version:** fail-safe — reducer runs in observe/pass-through, enforces
  nothing, until output replacement is re-verified and the allowlist updated. Auto-update cannot
  silently keep enforcing on an unverified version.

## Fallback behavior

- Reducer/hook error, CAS write failure, non-exact recovery, non-beneficial reduction, foreign cwd
  without `PYTHONPATH`, or unrecognized response shape ⇒ **native output passes through**. The agent
  is never worse off than with no ContextRuntime installed.
- `result://` recovery is always available (CLI `context_expand` + MCP), falling back to the live CAS.

## Production telemetry the runtime MUST keep collecting

Per session, continue emitting (already wired in the harness): **T_total** (input + output +
cache-creation + cache-read) and **turns**; per candidate the decision log (`enforced`,
`representation`, `fingerprint`, `raw_tokens`, `reduced_tokens`, `saved_tokens`, `non_beneficial`);
and the recovery + friction counters **`result_expansions`, `re_searches`, `native_rereads`**. These
are the guardrails that will catch a real recovery penalty in the wild and let production confirm (or
refute) the long-session compounding upside at scale.

## Frozen implementation architecture (B1 production path)

```text
Native tool output
      │
      ▼
Local safety / eligibility gate ── uncertain / unsafe / small / unversioned ──► passthrough
      │
      ▼
Transparent search reducer  (Graph ranking = OFF)
      ├── diagnostics preserved
      ├── path/line evidence preserved within budget
      ├── per-file rollup
      └── exact result:// recovery handle
      │
      ▼
PostToolUse updatedToolOutput ──► model context
```

Everything past this point is product engineering (daemon/session lifecycle, configurable policy,
telemetry, `doctor`/diagnostics, install/update/uninstall, recovery UX, and eventually an adaptive
budget `B(x) = f(output size, file diversity, diagnostics, search type, working state)` with a
conservative floor) — built after this freeze, not by reopening the experiment.
