# B2 — the transparent-residency portfolio, measured (synthesis)

> **Correction (2026-10-05, post-hoc audit):**
>
> - **"System + tool definitions ~18% of the prefix, not ours"** was superseded. The later
>   decomposition (`corpus/analysis/prefix-decomposition-v2.json`) puts the fixed prefix at 73.0% of
>   Σ P_t (hard lower bound 66.9%) with a 42,451-token mean first-call prefix, and the prefix doctor
>   rates 24,222 of the 42,995-token lean startup prefix (56%) as controllable
>   (`corpus/analysis/prefix-doctor-v1.json`). Prefix hygiene became the program's lever #1
>   (`docs/path-to-50.md` §5, `docs/prefix-doctor-findings.md`), so the conclusion's premise of a
>   fixed "~18% system floor we do not own" no longer holds.
> - **Turn axis.** The bash/test (0.48% / 2.46%) and retroactive-file (3.88% / 8.59% GROSS) ceilings
>   come from `corpus/bash_output_replay.py` and `corpus/retroactive_replay.py`, which count
>   transcript records as turns (the 2026-08-23 requestId merge was never back-ported) while dividing
>   by the CLI-reported T_total. Their compounding multipliers are in records (1.50–1.89 per real API
>   call across the four tasks), so these ceilings are biased upward; they were not re-measured
>   (transcripts not on this machine). The "45-turn sessions" are CLI turns (A_native mean 45.1); the
>   code's multiplier used the larger record count. The B2.0 file ceilings carry the same caveat
>   (`docs/b2-prefix-reduction-scope.md`).

The reviewer reframed B2 as *"a portfolio of safe residency controls, not one compression trick."*
That framing is right. This is the portfolio, each lever measured zero-quota (compounding-aware —
`Value = Tokens × RemainingTurns` — and edit/failure-safe) on the 19 native Step-7 coding sessions.

| lever | safe whole-session `T_total` saving | why it is capped |
|---|---:|---|
| **B1 search output** | **0.03%** (measured live, Step 7) | search outputs are a rounding error vs the prefix |
| **B2 file reads (prospective)** | **~4% ceiling → ~0 net** | 78% of edits force a re-read (B2.3); the re-read brings the full file back + wastes the compact read |
| **B2 bash/test output** | **0.48% mean, 2.46% max** | bash is 57% of tool output but only 19% is test-like; failures/tracebacks must be kept; tests run late so little compounding |
| **B2.v2 retroactive file** (compact-after-last-touch) | **3.88% mean GROSS, 8.59% max** | SAFE — no edit-recall (the file is done); but GROSS ignores the cache-WRITE of rewriting the prefix, so net is less, and it needs a history-transform mechanism |
| system + tool definitions | ~18% of the prefix, **not ours** [corrected 2026-10-05: 66.9–73.0% of Σ P_t, about half of it controllable; see top] | Claude Code's own prompt — not reachable by transparent interception |
| old conversation history | unmeasured | overlaps Claude Code's native `/compact`; fuzzy "last reference" safety criterion |

## Conclusion

**Prospective transparent tool-output residency reduction is exhausted, and it is not the
whole-session token lever for coding agents.** Three independent evidence-gated negatives (search
0.03%; prospective file self-negating; bash/test 0.48%) triangulate the same wall: the re-read
prefix is dominated by content that is **fixed** (the ~18% system floor we do not own [corrected
2026-10-05: superseded; see top]), **edit-
relevant** (must stay full — the edit-recall result), or **necessary** (diffs, tracebacks, git). The
verbose-and-safely-removable slice (huge greps, passing test noise) sums to **well under 1% net**.

This does not diminish what B1 is: a **safe, transparent, exactly-recoverable** tool-output reducer,
frozen and shippable (`B1_DECISION.md`). It re-scopes the *token-cost mission*: it will not come from
*prospective* transparent tool-output residency.

**The retroactive lever, now measured (`corpus/retroactive_replay.py`).** Compacting a file only
AFTER its last touch is the one model that is both SAFE (the file is done — no edit-recall problem)
and non-trivial: **GROSS 3.88% mean / 8.59% max** whole-session `T_total` on these 45-turn sessions.
But GROSS is a cache-READ ceiling; the NET subtracts the cache-WRITE of rewriting the prefix at each
compaction (~one prefix per event), which is precisely the hard part — and it needs a history
transform, not the PostToolUse hook, and it overlaps Claude Code's native `/compact`. So the best
safe residency lever is a **low-single-digit NET** on ordinary coding tasks (more on long sessions,
by the `Tokens × RemainingTurns` compounding), bought with a significant mechanism.

## Verdict

The whole transparent-residency portfolio, measured: **prospective is exhausted (<1% net); the one
safe non-trivial lever is retroactive-file at ~3.88% gross → low-single-digit net, for a hard build.**
None of it is the 25–40% lever on ordinary sessions; the compounding says the real upside lives in
long (100s-turn) sessions, unproven on this corpus. This is a portfolio of *small, safe* savings, not
a compression breakthrough — exactly the reviewer's "cumulative, not one magical 40% lever," now with
numbers.

## The honest options from here

1. **Retire the token-cost mission on this paradigm.** Ship B1 as safe/transparent infrastructure;
   value ContextRuntime for safety + exact recovery + transparency + observability, not cost.
2. **Build B2.v2 retroactive-file compaction** for the ~2–3% net (more on long sessions), accepting
   the cache-economics + history-transform mechanism — and first prove the long-session upside on a
   long-session workload, since 3.88% gross on 45-turn tasks is the floor, not the ceiling, of the
   `Tokens × RemainingTurns` argument.
3. **Reframe the value proposition** to safety/transparency/observability, treating cost as incidental.

The evidence-gated method did its job four more times — it kept us from shipping (or running live)
levers that don't pay, and it put a real number on the one that might. `reduce_file` /
`fileeligibility` / the replay harnesses remain in-tree as the building blocks a retroactive model
would reuse.
