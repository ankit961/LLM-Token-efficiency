# B1_DECISION — transparent search-output reduction (FROZEN)

**Status: research phase complete. This freezes the B1 policy and ends experiment-methodology
changes.** ContextRuntime B1 is now a product-engineering project. Evidence: `docs/step4..step7`,
`docs/step6.1-evidence-retention-findings.md`, `docs/step7-live-findings.md`.

## Decision

- **Default policy: `(budget=256, floor=125)`.** The conservative Pareto winner — it changes *only
  the floor* from shipped `(256,400)`, dominates it on both offline axes (paired reduction
  0.121 → 0.183; most inline evidence of any budget, ~7.4 retained match-lines/fired event), and
  showed **no recovery penalty and no wall/completion regression** live.
- **Graph ranking: OFF on the B1 search-reduction path.** Deprioritized, not deleted — Graph-Lite
  stays in ContextRuntime for SemanticFS / symbol bundles / dependency + task-state reasoning.
- **Both `CR_REDUCE_BUDGET` and `CR_REDUCE_FLOOR` remain runtime-configurable** (defaults above);
  do not hard-code. This is the seam the future adaptive policy `B(x)` plugs into.

## Confidence and the honest value statement

**What is proven:** transparent interception works; exact `result://` recovery works; the reducer
compresses **12.1%** of search-output tokens at the shipped setting (Step 6, measured on 184 real
outputs) and up to ~54% aggressively; it is **safe** — every safety invariant holds, and across 60
live sessions there were **zero** `result://` expansions, **zero** repeated searches, no rise in
native re-reads under D1, and wall time within ~4% of native.

**What is NOT true — and cannot be, via search reduction alone:** a whole-session token saving.
Live `T_total` is **98.8% correlated with turn count** (~71k tokens/turn — the cache-dominated
prefix re-read every turn), while the reducer's direct saving is **~935 tok/session = 0.028% of
`T_total`**. The `+10%/+20%` ΔT_total seen for D1/D2 is trajectory-length variance, not reduction.
So B1 does **not** clear the "consistent whole-session context-burden reduction" gate, and the
magnitude argument says it never will on its own: **search outputs are a rounding error against the
prefix.** Ship B1 as *safe, transparent per-read compaction* (which also declutters the
model-visible payload and compounds toward ~5–10% on long, grep-heavy sessions — n=1 task, not
established), **not** as a whole-session cost optimizer.

**Caveats:** 4 django tasks, sonnet, 5 reps; task success graded later (patches saved); the
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
