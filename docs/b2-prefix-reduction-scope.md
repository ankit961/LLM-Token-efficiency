# B2 — prefix reduction (scope)

**Premise (from `B1_DECISION.md` / `docs/step7-live-findings.md`).** Whole-session `T_total` is
98.8% turns × cached-prefix size (~71k tok/turn). Search-output reduction (B1) touches a rounding
error of that prefix, so it is safe+transparent but not a cost lever. **The lever is the re-read
prefix.** B2 targets the largest reducible, re-read component of that prefix.

## Where the prefix tokens are (measured, zero-quota, over the 20 native Step-7 sessions)

Reducible **tool-read** tokens by representation (the gate's semantic types):

| representation | tokens | share | events | status |
|---|---:|---:|---:|---|
| **file** (Read) | 117,910 | **73.9%** | 193 | **B2 primary target** |
| search (Grep/Glob/grep-Bash) | 41,493 | 26.0% | 200 | B1 — done |
| path_listing | 121 | 0.1% | 54 | B1 — done |

So **file reads are ~3× the bucket B1 handled** and are the obvious next target. Two more prefix
components a single long, test-heavy session (11138) exposes, that are NOT in the file/search bucket:

- **Bash/execution output** (test runs) — can be the single largest tool-output category (~60% of one
  session's tool outputs). High-value but **high-risk** (test *failures* are decision-critical).
  Secondary target, later, with a "keep failures/errors verbatim, summarize the passing tail" model.
- **System prompt + tool definitions** — ~13k tokens (first-turn cache-creation). A **fixed floor we
  do not own** (Claude Code's own prompt); it bounds the achievable prefix reduction and must be
  subtracted before claiming a percentage.

## Why B2 could clear the gate B1 could not

B1's direct saving was 0.028% of `T_total` — three orders of magnitude too small. File reads are
different: in the 11138 session, accumulated file-read tokens (~9.4k) were ~13% of a ~71k/turn
prefix. Reducing them ~80% (B1-style) would shave ~10% off the *per-turn* prefix, which then
**compounds over every remaining turn** — plausibly a **~10% whole-session `T_total`** reduction on
read-heavy sessions, i.e. exactly the "reliable 10–15% with unchanged success" bar the reviewer set
as valuable. **This is a hypothesis to measure in B2.0, not a claim** — the full per-turn prefix
decomposition (system vs file vs bash vs history) must be measured before building.

## The central new challenge — file reads are EDIT TARGETS

Search outputs are listings; dropping a line is cheap and recoverable. **A file read is the source
the agent will edit** — dropping the wrong lines can break an `Edit` (exact-context match) or hide
the code the agent needs to reason about. B1's safety model does not transfer. B2 needs a
file-read-specific model:

- **Never reduce an edit-imminent or recently-edited file.** A file the agent Edits/Writes (or is
  about to) must stay full — the edit needs exact surrounding context.
- **Prefer reducing STALE / reference-only reads:** a file read early and never subsequently edited,
  or superseded by a later fuller read of the same file, is the safe candidate. Keep a signature /
  structure skeleton (defs, imports, the read-around region) + `result://` for the body.
- **Keep the "read-around" region** the agent is actively working in; page the rest.
- The real safety metric is **edit-recall**, not line-recall: did reduction ever drop a line the
  agent *subsequently edited or depended on*? (The B2 analog of Step 6.1, but the outcome is edits,
  which is what actually breaks.)

## The other new constraint — cache economics of retroactive compaction

The prefix is cheap-per-turn precisely because it is **cached** (`cache_read`). Two mechanisms:

- **Prospective** (reduce a large file read *as it enters*, like B1's PostToolUse hook): **cache-safe**
  — it only shapes new content; the B1 architecture extends directly. Cost: you must decide before
  you know whether the agent will edit that file.
- **Retroactive** (compact a *stale* read already in history): the biggest theoretical win, but it
  **rewrites the cached prefix → pays `cache_write` and invalidates cache-read for later turns**.
  Only worth it when `cache_write_now < Σ cache_read_saved` over remaining turns, and it likely needs
  a mechanism beyond PostToolUse (session-transform / compaction pass). **Defer to B2.v2.**

**B2.v1 = prospective file-read reduction** (cache-safe, reuses B1 wholesale). Retroactive
stale-read compaction is a separate, later capability gated on the cache-economics math.

## Reuse from B1 (do not rebuild)

Prospective eligibility gate, transparent PostToolUse replacement, confirmed CAS + byte-exact
`result://` recovery, fail-open runtime version gate, decision log + telemetry, beneficial-only
guard, budget/floor config. B2 adds a `file` representation path with its own eligibility (edit-state
aware) and reducer (structure-preserving), not a new framework.

## Evidence-gated build order (mirrors B1 — measure → deterministic safety → replay → live)

- **B2.0 — decompose the prefix at scale (zero-quota).** Full per-turn prefix breakdown (system+tools
  vs file vs bash vs search vs history) across the 60 Step-7 + 16 pilot transcripts; quantify the
  file-read share of the *actual per-turn prefix* and the reduction ceiling after subtracting the
  fixed system floor. **Go/no-go gate:** proceed only if file reads are a material, compressible
  slice of the per-turn prefix.
- **B2.1 — file-read safety model (deterministic).** Edit-state-aware gate: never reduce
  edit-imminent/recently-edited/small files; classify stale/reference-only reads; keep read-around +
  structure. Adversarial tests (a reduced read must never break a subsequent Edit).
- **B2.2 — prospective structure-preserving file reducer.** Signature/skeleton + read-around within
  budget + `result://` body; beneficial-only; exact recovery. Reuses `reduce_*` scaffolding.
- **B2.3 — offline paired counterfactual replay (edit-recall).** Replay the retained file trajectories
  through the reducer; measure whether any subsequently-edited/depended-on line was dropped
  (edit-recall = the safety number) and the paired token reduction on the file bucket.
- **B2.4 — one live A/D** (A=native vs D=B1-search + B2-file), same harness (`step7_live_experiment`),
  same T_total/turns/rereads/expansions/success metrics, 3→5 reps. This is the one that can show a
  material whole-session win, because file reads are 74% of the read bucket.

## Honest expected-value bounds (set now, verify in B2.0/B2.4)

- Upper bound if file reads are ~13% of the per-turn prefix and reduce ~80%: **~10% whole-session
  `T_total`**, compounding with session length. Realistically less after the edit-safety gate spares
  edit-imminent files and after the fixed system floor is subtracted.
- The safety bar is stricter than B1: a dropped edit-relevant line is a *correctness* risk, not just a
  recovery cost. B2 must fail-open aggressively (when in doubt about edit-state, do not reduce).

## B2.0 RESULT (measured 2026-08-18) — WEAK-GO, ceiling far below the 10–15% bar

`corpus/prefix_decomposition.py` `reduction_ceiling_over_runs`, over the 19 native Step-7 sessions,
compounding-aware (a read at turn *t* is cache-read every later turn; reducing it saves
`0.8 × tokens × remaining_turns`), edit-safe (spare any file the agent later Edits/Writes):

- **72% of file reads are EDIT TARGETS.** Mean 8.3 file reads/session, of which only **2.3 are
  reducible** (reference-only) and **6.0 must be spared** (the agent edits them). On coding tasks the
  agent reads mostly what it is about to change.
- **Edit-safe reducible ceiling = ~2.0% of whole-session `T_total`** (compounding-aware, whole-file
  sparing), highly variable: 0% on 5/19 sessions, up to 7.8%. The *unsafe* raw figure (reduce all
  file reads, ignore edit-safety) is 7.5% — not shippable.
- A line-level reducer (spare only the edited lines + read-around inside an edited file, reduce the
  rest) could reclaim part of the 2.0% → 7.5% gap — realistically **~3–5%**, still below the bar and
  requiring the full edit-safety machinery to earn it.

**Verdict: transparent per-tool-output reduction has a structurally low whole-session ceiling** —
search 0.03% (B1), edit-safe file reads ~2–5% (B2.0) — because the re-read prefix is dominated by
content that is either **fixed** (the ~13k system+tools floor we do not own) or **must be preserved**
(edit targets, and test *failures* in bash output). File-read reduction is real, safe, and compounds
on long read-heavy sessions, but it is a **~2–5% lever, not a 10–15% one.** The next-highest
untested lever is **bash/test-output** (not edit targets; largest category in the one test-heavy
session, ~60%) under a "keep failures/errors, summarize the passing tail" model — it deserves its
own go/no-go before committing to build B2.1–B2.4. This is a decision input, not a green light.

## Non-goals for B2.v1

Retroactive stale-read compaction (cache-economics, B2.v2); bash/test-output reduction (high-risk,
later); conversation-history compaction (Claude Code's native `/compact` domain — complementary, not
B2); the adaptive budget `B(x)` (only after the fixed policy ships and is measured).
