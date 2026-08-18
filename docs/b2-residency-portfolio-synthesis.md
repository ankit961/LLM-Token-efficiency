# B2 — the transparent-residency portfolio, measured (synthesis)

The reviewer reframed B2 as *"a portfolio of safe residency controls, not one compression trick."*
That framing is right. This is the portfolio, each lever measured zero-quota (compounding-aware —
`Value = Tokens × RemainingTurns` — and edit/failure-safe) on the 19 native Step-7 coding sessions.

| lever | safe whole-session `T_total` saving | why it is capped |
|---|---:|---|
| **B1 search output** | **0.03%** (measured live, Step 7) | search outputs are a rounding error vs the prefix |
| **B2 file reads (prospective)** | **~4% ceiling → ~0 net** | 78% of edits force a re-read (B2.3); the re-read brings the full file back + wastes the compact read |
| **B2 bash/test output** | **0.48% mean, 2.46% max** | bash is 57% of tool output but only 19% is test-like; failures/tracebacks must be kept; tests run late so little compounding |
| system + tool definitions | ~18% of the prefix, **not ours** | Claude Code's own prompt — not reachable by transparent interception |
| conversation history / stale-file | **unmeasured** | only reachable RETROACTIVELY (cache-write + invalidation), overlaps native `/compact` — B3/B2.v2 |

## Conclusion

**Prospective transparent tool-output residency reduction is exhausted, and it is not the
whole-session token lever for coding agents.** Three independent evidence-gated negatives (search
0.03%; prospective file self-negating; bash/test 0.48%) triangulate the same wall: the re-read
prefix is dominated by content that is **fixed** (the ~18% system floor we do not own), **edit-
relevant** (must stay full — the edit-recall result), or **necessary** (diffs, tracebacks, git). The
verbose-and-safely-removable slice (huge greps, passing test noise) sums to **well under 1% net**.

This does not diminish what B1 is: a **safe, transparent, exactly-recoverable** tool-output reducer,
frozen and shippable (`B1_DECISION.md`). It re-scopes the *token-cost mission*: it will not come from
transparent tool-output residency. The only residency lever left is **retroactive** — compacting
stale file reads and old conversation turns once they go "done" — which is a harder, different
mechanism (cache-write-vs-read economics + a history transform, not PostToolUse) that overlaps Claude
Code's native `/compact`, and whose ceiling is unmeasured.

## The honest options from here

1. **Retire the token-cost mission on this paradigm.** Ship B1 as safe/transparent infrastructure;
   value ContextRuntime for safety + exact recovery + transparency, not cost. Stop here.
2. **Measure the retroactive ceiling (zero-quota)** — stale-file + old-history residency — as the
   last lever before deciding whether the harder mechanism is worth building. This is the only
   remaining place a material (long-session) saving could live.
3. **Reframe the value proposition** to safety/transparency/observability, treating any cost saving
   as incidental.

The evidence-gated method did its job three more times — it kept us from shipping (or running live)
levers that don't pay. `reduce_file` / `fileeligibility` / the replay harnesses remain in-tree as the
building blocks a retroactive model would reuse.
