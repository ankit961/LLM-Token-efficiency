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
- Three ceilings (compounding-aware, whole-session `T_total`), by how edited files are handled:
  - **B2.0 spare** (keep any edited file full all session): **mean 2.0%**.
  - **RESIDENCY / compact-until-edit** (compact a file from read until the edit materializes exact
    content, then it re-enters — the model B2 actually implements): **mean 4.0%, max 12.5%**, highly
    variable (near-0 on non-read-heavy sessions).
  - **raw** (reduce everything, ignore edit-safety, not shippable): **mean 7.5%**.
  The realistic, shippable number is the **residency ~4% mean / ~12.5% on read-heavy sessions**.

**Verdict: residency control IS the right lever, but its magnitude scales with session length.** On
these **45-turn** SWE-bench tasks the residency ceiling is **~4% mean / ~12.5% max** — already far
above search (0.03%, B1) and whole-file sparing (2%), confirming the `Value = Tokens × RemainingTurns`
thesis. The headline **25–40%** the product targets lives in **long (100s-of-turn) sessions**, where
a file admitted early is carried for far more turns — an *extrapolation* our short-task corpus cannot
prove, so the B2.4 live A/B should ultimately run a long-session workload to demonstrate it while
these tasks validate **safety + direction**. Two hard limits remain regardless: the ~13k system+tools
floor is fixed (not ours), and edit targets (72% of reads) plus test *failures* must be preserved.
Proceeding to build B2.1–B2.4 (residency control for file reads) on this basis; **bash/test-output**
reduction (keep-failures/summarize-passes) is a separate later lever.

## B2.3 RESULT (measured 2026-08-18) — PROSPECTIVE compaction FAILS the edit-recall gate

`corpus/edit_recall_replay.py` over the 20 native Step-7 transcripts (agent saw FULL content, then
edited): if B2 had compacted the read the agent used, would the edit's `old_string` still be in the
skeleton? 96 real edits scored.

- **Edit-recall (B2.2-gated, spare already-edited files): 21.9%** — **78% of edits force a re-read**.
  Ungated (compact every read): 8.3%. **Budget-insensitive** (512/1024/2048 identical): the skeleton
  keeps *signatures*, but edits target *bodies*, which drop regardless of budget.
- **59% of edits are FIRST edits** of their file — the structural trap: the pre-edit read was
  compacted, so the agent never saw the body it is about to change and must re-read first. Sparing
  already-edited files (B2.2) cannot help the first edit, and every read of a not-yet-edited file is
  compacted, so the agent cannot obtain the body prospectively at all (B1 also showed agents almost
  never call `result://` — 0 expansions in 60 sessions).

**Verdict: prospective signature-skeleton file compaction does not work for edit-heavy coding.** It
is never a *correctness* break (exact raw in CAS; Edit matches disk), but it forces a re-read on the
majority of edits — the re-read brings the full file back and adds the wasted compact read, so the
~4% residency saving is negated (net likely *worse*, like B1 but with extra reads). Do **not** take
this to a live A/B. **The residency lever survives only under a RETROACTIVE / stale-read model**:
compact a file's residency AFTER the agent is *done* editing it (so bodies are present while
editing, dropped only once the file goes reference-only). That is the deferred **B2.v2**, gated on
the cache-write-vs-cache-read-saved economics and a history-transform mechanism (not PostToolUse).
Its ceiling is bounded by how much of the session a file spends "done" — to be measured before build.

## Non-goals for B2.v1

Retroactive stale-read compaction (cache-economics, B2.v2); bash/test-output reduction (high-risk,
later); conversation-history compaction (Claude Code's native `/compact` domain — complementary, not
B2); the adaptive budget `B(x)` (only after the fixed policy ships and is measured).
