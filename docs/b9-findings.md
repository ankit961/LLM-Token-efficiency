# B9 — Local-SLM arm + Claude re-confirmation: **−20.2% pooled dollars re-confirmed (9/9); the OpenAI-native local harness is built, tested, and runnable — its A/B is not yet run**

**2026-09. Two arms, different maturity.** Arm 1 re-runs the surviving admission+lifetime stack on
fresh Claude Sonnet (Max) sessions and reproduces the program result. Arm 2 is a new, self-contained
OpenAI-format agent harness that carries the *same* two levers into a loop against a self-hosted
model, so the treatment effect can be measured *within one model* (no cross-client confound). Arm 2
is complete and unit-tested; the live A/B awaits a local endpoint + a django mirror.

Artifacts: `corpus/analysis/b9-claude-arm/results-{N,T}.json` (Arm 1);
`corpus/local_agent_ab.py`, `tests/test_b9_local_agent.py`,
`corpus/analysis/b9-local-arm/config.example.json` (Arm 2);
`contextruntime/providers.py::local-serving` (the cost-axis profile).

## Arm 1 — Claude re-confirmation A/B (LIVE, measured)

3 pairs; each pair = one graded session of three chained django tasks; **N** = native `claude -p`,
**T** = `--disallowedTools` admission + gateway ENFORCE (`CR_GATEWAY_CACHE_ALIGN=gated`). Same
protocol as B8v2. BITE = list-price input cost (read 0.1 / 1h-write 2.0 / output 5.0).

| pair | N BITE | T BITE | Δ BITE | N Σinput | T Σinput | Δ Σinput |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 587,218 | 505,746 | −13.9% | 3,398,688 | 2,811,409 | −17.3% |
| 1 | 619,666 | 684,038 | +10.4% | 3,718,833 | 4,092,668 | +10.1% |
| 2 | 825,264 | 431,920 | −47.7% | 5,239,438 | 2,247,570 | −57.1% |
| **pooled** | **2,032,148** | **1,621,703** | **−20.2%** | **12,356,959** | **9,151,647** | **−25.9%** |

- **Pooled dollars −20.2%** (CLI cost report agrees: $6.10 N vs $4.87 T = −20.2%). Residency Σinput
  **−25.9%**; cache-read −26.1%; cache-creation −20.9%.
- **Quality 9/9 vs 9/9** graded task-instances — non-inferior, perfect in both arms.
- Per-pair spread (−47.7% … +10.4%) is the familiar same-task rep variance; the pooled ratio is the
  endpoint. Pair 1 going the "wrong" way on one rep is within that variance and does not move the
  pooled result out of band.

**Decomposition, honestly — the saving is essentially all *admission*.** Every T pair logged
`fires: 1 {cold-start}`, **`retired: 0`, `thinking_stripped: 0`**. At 1h-cache write prices the
gated scheduler's break-even never cleared mid-session, so retirement and thinking-GC contributed
zero — the scheduler declining a mutation that would not pay, exactly as designed (its no-harm
property). So B9's Claude arm **re-confirms the entry-fee lever** (admission ≈ the whole −20.2%); it
does **not** exercise retirement. Measuring retirement in isolation is precisely what Arm 2 is for.

## Arm 2 — Local-SLM OpenAI-native harness (BUILT + TESTED; A/B not yet run)

**Why a separate harness.** Claude Code and our gateway both speak Anthropic Messages; a self-hosted
Ollama/vLLM server speaks OpenAI chat (`tool_calls` / `role:"tool"`). Routing Claude Code through a
shape-translator is the fragile path the release plan warns against. Instead this is the *common
controllable harness*: a small agent loop that talks OpenAI to the local endpoint, with admission +
retirement applied **in the loop**, comparing native-local vs treatment-local **within one model**.
Cross-client (local vs Claude) numbers mix harness and model and are reported as such — never as a
pure model effect.

**The two levers, in-loop** (`corpus/local_agent_ab.py`):
- **Admission** — the treatment ships only the tools the task needs ("no unused schemas"). On the
  local harness the whole set is already lean (3 tools: read/write/bash), so `TREATMENT_TOOLS ==
  TOOLS` and admission is a **deliberate no-op** here. Consequence: the local A/B **isolates
  retirement** — the clean measurement B9's Claude arm could not produce.
- **Retirement** — `retire()` on OpenAI format: for each supersession key (`path:` for read/write,
  `bash:` for a command) keep only the *latest* tool result; replace earlier ones with
  `"[retired: superseded by a later call; re-read/re-run if needed]"`. Safe-by-construction (only
  stubs when a newer copy exists → recoverable), idempotent, and format-valid (it stubs the tool
  *content*, never removes the `role:"tool"` message, so every `tool_call_id` still resolves).

**Cost axis = residency (Σ prompt_tokens).** Self-hosted serving has no cache-$ split: every prompt
token costs the same prefill compute whether cached or not, so the axes are TTFT / VRAM / throughput,
not dollars. Encoded in the new `local-serving` provider profile (`read=write=out=1.0, ttl=0` →
`break_even_reads=0`: on your own stack mutation *always* reduces prefill work — the exact opposite
of `anthropic-1h`'s break-even of 19). Grading is the real B6 native grader
(`corpus/b6_grading.grade`): reset the official test files the agent touched, apply `test_patch`, run
`FAIL_TO_PASS` + `PASS_TO_PASS` under python3.11.

**Two engineering fixes landed this cycle:**
1. **Retirement counter bug (fixed).** The loop now *persists* the pruned history
   (`messages, n = retire(messages)`) instead of retiring a throwaway copy each turn. Because
   `retire()` is idempotent, `n` counts only results newly retired *this* turn, so `retired` is the
   true **distinct** count — and retirement is byte-stable across turns, matching the gateway's
   cache-alignment invariant. (The pre-fix loop left `messages` full and re-counted every standing
   stub each turn, inflating `retired`; it never touched Arm 1's numbers, which are gateway-measured,
   but it would have mis-reported the local arm's headline diagnostic.) Regression test
   `test_treatment_retires_persistently_and_does_not_overcount` pins `retired == 1` where the old
   code reported 3.
2. **`ab` mode + grading wired.** `python -m corpus.local_agent_ab ab <config.json>` runs the paired
   N-vs-T A/B (fresh git worktree per task/rep/arm → local agent loop → B6 grading → resumable JSON →
   pooled residency summary), mirroring the proven `corpus/b6_live_ab.py` loop. Four tests green,
   incl. an end-to-end `ab()` plumbing test (real git worktree + fake model, no django needed).

**Status: runnable, not yet run.** Set `endpoint`, `model`, and `mirror` in
`corpus/analysis/b9-local-arm/config.example.json` and go. The live A/B needs (a) a local
OpenAI-compatible endpoint and (b) a django clone with the base commits — both environment/data, not
code. **No local-SLM residency number exists yet; do not cite one.**

## What is measured vs not

| claim | status |
|---|---|
| Admission re-confirmed on fresh Claude Sonnet sessions: −20.2% $ / −25.9% residency, quality 9/9 | **LIVE** — Arm 1, 3 pairs |
| Retirement fired / contributed in the Claude arm | **No** — `retired: 0` on all 3 T pairs (all-admission) |
| OpenAI-native admission + retirement harness, provably-safe `retire()`, real grading | **BUILT + UNIT-TESTED** — Arm 2 |
| Local-SLM residency reduction from retirement-in-isolation | **NOT YET RUN** — needs endpoint + mirror |

## Why finish the local arm (design rationale, not a measurement)

A session survey of the external context-retention literature places the local-SLM arm as a
strategic moat rather than a convenience:

- **The highest-ratio, information-preserving methods are physically impossible over a closed text
  API.** All KV-cache methods (StreamingLLM, H2O, SnapKV, KIVI 2-bit quant, KVzip, VeriCache) and all
  soft-prompt/activation methods (Gist, ICAE, 500xCompressor: 26×–480×) read attention/KV tensors or
  inject embeddings — which the Messages/Chat API never exposes. They are **local-serving-only**, and
  that class includes the only *provably lossless* compute method surveyed (VeriCache: draft-from-
  compressed / verify-against-full). The closed-API gateway is capped at what it can do to *text*;
  the local arm additionally commands the cache and activation layers.
- **On the closed-API side, retire-with-recovery is now vendor-native.** Anthropic *context editing*
  (`clear_tool_uses_20250919`) retires the oldest tool results, **leaves placeholder recovery-hint
  text**, and keeps the client's full history — i.e. a server-side implementation of this project's
  own retirement+recovery design, protecting the prompt cache via `clear_at_least`. It is prior-art
  validation of the thesis and a plug-and-play path for the Anthropic arm; the "retain without losing
  info" guarantee comes from pairing it with a memory/RAG store the recovery hint keys into.

Admission + lifetime control is the shared spine across both arms; the local arm simply has more
organs. Finishing Arm 2's A/B is what turns "retirement should pay on a free-write provider"
(`break_even_reads=0`) from a modeled prediction into a measurement.

---

*Engineering posture unchanged: version-gated, inferred, best-effort, fail-open, verify-at-runtime.
Every number here is design-partner evidence pending replication; the local-SLM residency line is
explicitly absent until the A/B runs.*
