# Step-5 pilot — findings (4 tasks × A/B/C, 16 live sonnet sessions)

**Run 2026-08-17** over the 4 highest search-token django tasks from Step-4 (`10554, 11138, 12419,
14608`), each × `A_native / B_shipped(256/400) / B_tuned(64/400) / C_graph(256/400)`, real headless
`claude -p` (sonnet, 600s cap) via `Step5Runner`. All 16 completed; no crashes.

## Per-task effective read tokens + reductions

| task | A_native | B_shipped | B_tuned | C_graph | reductions (A/Bs/Bt/C) | Δtok(B_shipped−A) | wall ratio | C valid? |
|---|---|---|---|---|---|---|---|---|
| 10554 | 8,581 | 8,665 | 10,563 | 9,979 | 0/1/2/0 | **−84 (−1%)** | 1.66 | ❌ |
| 11138 | 14,785 | 10,677 | 15,437 | 19,959 | 0/4/4/0 | **+4,108 (+28%)** | 1.00 | ❌ |
| 12419 | 14,997 | 4,467 | 3,905 | 3,116 | 0/3/0/0 | **+10,530 (+70%)** | 0.58 | ❌ |
| 14608 | 7,739 | 6,865 | 5,188 | 5,064 | 0/0/0/0 | **+874 (+11%)** | 1.36 | ❌ |

## What the pilot establishes

1. **The mechanism is correct and safe in live sessions.** 14 reductions fired across the 16
   sessions; the canary (separately) confirmed the journal's `model_visible_tokens` equals the
   reduced size. No crashes, no safety incidents, tasks ran and produced patches in every arm.
2. **The clean, trajectory-independent number: when it fires, it removes ~80%.** Summed over the 14
   fired reductions (from the decision logs, so unconfounded by trajectory): **raw 14,364 →
   reduced 2,861 tokens, i.e. 11,503 tokens removed (80%)**; mean per reduction 1,026 → 204.
3. **Whole-session Δtokens(B−A) is NOT measurable at n=1.** It spans −1% … +70% and tracks
   **session length** (wall ratio 0.58 … 1.66), not reduction count — `B_tuned` with the *same* 4
   reductions as `B_shipped` on 11138 went the *opposite* direction (+col). Trajectory variance
   (±thousands of tokens across independent sessions) dwarfs the per-session reduction savings.
4. **The graph arm (C) was never actually tested.** `graph_ranked == 0` across ALL sessions and
   `c_valid=false` for all 4 tasks: C's stochastic sessions happened to fire 0 reductions, so graph
   ranking never engaged. The harness's Step-5.2 validity guard correctly refused to report a C−B
   signal rather than fabricate one.
5. **Firing is concentrated and stochastic.** Reductions clustered on the search-heavy tasks
   (11138: 8, 12419: 3, 10554: 3) and none in 14608's enforce arms — consistent with Step-4's
   finding that most greps are small (median 125 tok) and reduction only bites the few big ones,
   which a given live session may or may not produce.

## Verdict

- **Mechanism**: ✅ real, safe, ~80% reduction on the reads it fires on.
- **Whole-task token win**: ❓ **inconclusive** — the single-run-per-arm design is too noisy.
- **Graph value (C−B)**: ❓ **untested** — graph never engaged.

This does **not** overturn Step-4's 12.1% estimate; it confirms the per-fired-read reduction is
large (~80%) and adds that realized whole-task savings depend on how often the agent produces big
greps — which this pilot could not pin down at n=1.

## What a conclusive Step-5 needs (methodology, not more edge-case fixes)

1. **Replicates** — ~5–10 runs per (task, arm) to average out trajectory variance so Δtokens(B−A)
   becomes a signal, not noise.
2. **Force C to actually test graph** — lower the floor (Step-4: 244 catches materially more),
   and/or pick tasks that reliably produce big greps, and/or seed the working set — so `graph_ranked>0`.
3. **Consider a paired design** — replay the *same* captured tool-call trace through simple vs graph
   reduction, isolating the mechanism from live-agent trajectory noise (a trace-replay, not a live
   A/B/C). The per-reduction `saved_tokens` sum (item 2 above) is the cleanest live proxy today.
