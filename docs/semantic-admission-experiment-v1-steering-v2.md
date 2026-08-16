# Semantic Admission Experiment v1 — steering redesign (brief v2-locate-then-examine)

**Status: a new versioned addendum, per the freeze condition in `semantic-admission-experiment-v1.md`
§10** ("any change to arm definitions... after seeing any Arm B result invalidates the paired
design... requires a new versioned document, never a silent edit"). We had seen Arm B results
(6 real runs) before making this change, so it is documented here rather than edited into the
original protocol. The original document, its arm definitions, and its identity constraints are
unchanged and remain the authority for everything except what this addendum explicitly supersedes.

## What was observed before this change

Across 6 real `semantic_directive` runs (run-02..07, collected after both the MCP-startup bug and
the bundle-overhead bug were found and fixed — see commits `0370b85`, `30feb00`), adoption was
**0/6**: zero `semantic_reads` in every run. This was not a mechanism failure — verified directly,
not inferred:

- The MCP server started correctly and `read_symbol` resolved real symbols with sane, in-budget
  output (post-fix).
- The steering brief was **confirmed delivered** for at least one run (`run-07`,
  `steering.brief_included=true`, `brief_chars=677`) via new logging added specifically to settle
  this (commit `85b4714`); the brief-delivery code path is subprocess-free (a relative import, no
  cwd dependency), so it cannot fail the way the MCP subprocess did — high confidence it was
  delivered for run-02..06 as well, even without the same direct log.

## Why (the diagnosis this addendum acts on)

The v1 brief's entire imperative was on `read_symbol(name)` — "call it FIRST". `read_symbol`
requires an **exact symbol name up front**. SWE-bench bug reports rarely supply one; they describe
a symptom or a traceback. `run-07`'s agent did **16 native reads** — consistent with a genuine
exploration/locate phase, not "I already know which function to open". `read_symbol` has no role
in that phase; the agent's only path forward was native Read/Grep, which is exactly what happened.

`context_search(query)` — free-text/keyword lookup returning handles, not full dumps — is the
actual semantic substitute for that exploratory phase. v1's brief mentioned it only as one of
several secondary tools, with no imperative attached.

## What changed (`contextruntime/policybrief.py`, `BRIEF_VERSION = "v2-locate-then-examine"`)

The brief now maps to the real two-phase workflow instead of one undifferentiated imperative:

1. **LOCATE** — "when you don't yet know which function/class is relevant..., call
   `context_search(query)` FIRST instead of a broad native Grep/Read — same move, smaller."
2. **EXAMINE** — "once you know the specific symbol, call `read_symbol(name)` instead of a native
   Read to examine it."

Everything else is unchanged: still delivered via `--append-system-prompt` (SessionStart channel
for daily-drive), still strictly advisory (native Read/Grep/Bash "always work and are never
blocked"), still no change to the `semantic_directive` *mechanism* (MCP enabled, native fallback
available, task prompt untouched). This is a content change to the nudge, not an arm-definition or
protocol-mechanics change — Arm B is still "SemanticFS MCP + directive brief + native fallback".

## Provenance

`ClaudeBackend`'s per-run `steering` log (commit `85b4714`) now also carries `brief_version`, so
every future run records which brief design produced its adoption data. The 6 exploratory runs
above used v1 implicitly (predating the version stamp) — their `run_index`/manifest entries should
be read as v1-brief data if ever reused; they are not part of any confirmatory collection.

## What this does NOT change

- Arm A (native) is completely unaffected — no brief, no MCP, byte-identical argv.
- The paired outcome analysis, token accounting, and freeze condition in the original protocol
  document all still apply unchanged.
- Arm C (`semantic_enforced`) remains reserved and unimplemented.

## Next step

Validate on a small batch again (the same cautious pattern used throughout this investigation)
before committing quota to a full 50-task collection under this brief version.
