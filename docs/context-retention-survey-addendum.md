# Context-Retention Survey — Addendum (deeper pass)

**2026-09-19.** New material found in a second, deeper search, beyond the 30 techniques in
[context-retention-survey.md](context-retention-survey.md). Focus: **lossless / recoverable
alternatives to lossy compaction** (the "don't release info like `/compact`" requirement) and
**plug-and-play** fit. Same three-state framing as the base survey: *lossless* (equivalent info) ·
*recoverable* (removed from the active prompt, kept verbatim elsewhere, paged back on demand — the
project's design point) · *lossy* (dropped, e.g. summarization).

**Recency honesty:** several items below are 2026 preprints (arXiv IDs `26xx.xxxxx`); their exact
numbers were read by a summarizer and should be verified on the source PDF before quoting. Vendor
benchmarks (LCM/Volt, TypeSafe) are flagged as such.

---

## 0. TypeSafe "System One" / Jev — does it help? (verdict)

**Not a context-retention technique.** Re-read confirms the post has *no* treatment of context
window, KV cache, prompt caching, or compression. So it does **not** help the "retain without losing
info" goal directly.

**Where it could fit:** as a cheap, fast, calibrated **policy/classifier** for the router's
*decisions* — "which tools to admit," "is this tool result safe to retire," "fire the gated
mutation or hold." Jev's advertised profile fits that role: **input "$0.042 / MTok", "Output tokens:
FREE", "70ms–500ms" end-to-end, "Calibrated: higher confidence means higher accuracy," type-safe
structured outputs.** Calibrated confidence is exactly what a *thresholded* retire/admit gate wants.

**Cost of adopting it:** it is **API-only** ("our service", `console.typesafe.ai`; no self-host,
no weights) → a new hosted dependency and data-egress surface. And it targets bounded "System One"
decisions, not stateful logic. **Verdict:** optional future *policy brain* for semantic admit/retire
judgments; **not needed for the core**, whose break-even math is already free and deterministic.
Treat as a candidate to A/B against a local classifier, not a dependency to take on now. *(All
figures are vendor claims; the post itself flags benchmark bias.)*

---

## 1. New techniques (beyond the base survey)

| Technique | Lossy / Recoverable / Lossless | Layer | Gateway-pluggable? | Local-only? | Relevance |
|---|---|---|---|---|---|
| **LCM — Lossless Context Management** (2605.04050) | **Recoverable (lossless at source)** | text/harness | ✅ yes | ❌ | **A published version of this project's design** — see §2 |
| **Squeez — task-conditioned tool-output pruning** (2604.04979) | Lossy but re-obtainable (re-run tool) | text/harness | ✅ yes | ❌ | Coding-agent tool-output pruning on SWE-bench; the learned-relevance complement to supersession retirement |
| **Provence — context pruning** (2501.16214, ICLR'25) | Recoverable (keep originals) | text-prompt | ✅ yes | ❌ | Pruning-as-sequence-labeling + reranking; robust RAG pruner; LLMLingua-2 successor |
| **Cartridges (self-study)** (2506.06266, HazyResearch) | Lossy (trained KV), versionable | KV / soft-prompt | ❌ no | ✅ yes | Train a tiny KV for a static corpus (repo map, style guide); **26× throughput**; local-arm moat |
| **CacheBlend** (EuroSys'25 best paper) | Near-lossless (selective recompute) | KV-cache | ❌ no | ✅ yes | Reuse KV of **non-prefix** chunks → ~100% KV hit for RAG; unlocks lossless-ish recovery-paging on the local arm |
| **CacheGen** | Lossy (KV quant+stream) | KV-cache | ❌ no | ✅ yes | Compress + stream KV for fast context load; pairs with CacheBlend |
| **Anthropic compaction** `compact_20260112` | **Lossy** (server-side summary) | provider-native | ✅ invoke | ❌ | The native equivalent of `/compact` — *the thing to use sparingly* |
| **Anthropic tool clearing** `clear_tool_uses_20250919` | **Recoverable** (placeholder + re-call) | provider-native | ✅ invoke | ❌ | Native retire-with-placeholder = your retirement lever, server-side |
| **Anthropic memory tool** `memory_20250818` | **Recoverable** (you own the store) | external-memory | ✅ invoke | ❌ | Write-before-clear → lossless page-back across sessions |
| **Rate–Distortion view of memory compaction** (2607.08032) | theory | — | — | — | Frames admission/retirement as an optimal keep/forget tradeoff |
| **Parallel Context Compaction** (2605.23296) | analysis | text | ✅ | ❌ | Finds summarization output is largely **input/prompt-invariant** → an argument *against* lossy summaries, *for* recoverable retention |
| **Contextual Memory Virtualisation** (2602.22402) | Recoverable ("structurally lossless trimming") | text/harness | ✅ yes | ❌ | DAG-based session state; trims structure, keeps content |
| **Memory as Action** (2510.12635) | Recoverable | text/harness | ✅ yes | ❌ | Agent curates its own context via explicit memory *actions* (RL) |

Surveys for the reading list: *LLM Agent Memory: A Unified Representation–Management Survey*
(Preprints 202603.0359); *Memory for Autonomous LLM Agents* (2603.07670); *A Survey of Agent Memory
in the Second Half* (2602.06052).

---

## 2. LCM — the published blueprint for this exact product

**LCM (Lossless Context Management)** is, effectively, this project's retirement+`recover(id)`
design, named and benchmarked. It is the single most relevant find.

- **Dual-state architecture** — an **Immutable Store** ("every user message, assistant response, and
  tool result is *persisted verbatim and never modified*", the "sole source of truth", typically in
  PostgreSQL) and an **Active Context** (the window sent each turn, "assembled from a mix of recent
  raw messages and precomputed summary nodes").
- **Recovery tools** — `lcm_grep` ("regex search across the full immutable message history") and
  `lcm_expand` ("expands a summary node into its constituent messages, reversing the compaction that
  created it"). **This is `recover(id)` plus a search-over-retired-store.**
- **Lossless guarantee** — *"For every message m produced during a session, the unsummarized
  original is retained verbatim in the immutable store and remains reachable."*
- **Plug-and-play** — "runs within [the] message-processing pipeline, requiring **no modifications
  to the model's tool definitions or prompt format**"; works over a normal text API. Harness-level,
  vendor-neutral — exactly the deployment shape of this project's gateway.
- **Two-threshold trigger** (τ_soft async / τ_hard blocking) + three-level escalation (summarize →
  aggressive summarize → deterministic 512-token truncate) with a guaranteed-convergence fallback.
- **Benchmark (vendor):** on OOLONG long-context reasoning, LCM's product ("Volt") reportedly scores
  **74.8 vs Claude Code 70.3**, widening at length (1M tokens: +51.3 vs +47.0). Vendor result — treat
  as directional, not independent.

**Takeaway:** the "complete, plug-and-play, no-info-released" product already has a published
reference design. This project's `recover(id)` = `lcm_expand`; the missing piece is
`lcm_grep` — **regex/semantic search over the retired store** so the model can find a retired result
without knowing its id. That is the highest-value next feature.

---

## 3. The line the user is drawing is Anthropic's own line

Anthropic's context-engineering cookbook makes **exactly** the lossy-vs-lossless distinction behind
"retain without releasing info like `/compact`":

- **Compaction** (`compact_20260112`, beta `compact-2026-01-12`) is **"lossy by design"** — a
  whole-transcript summary that **replaces** history ("once compaction fires, the original message
  history is gone"). Probe: high-level facts 3/3 kept, obscure specifics 0/3. **This is `/compact`.**
  ~50% peak-token reduction.
- **Tool-result clearing** (`clear_tool_uses_20250919`) is **recoverable**: it replaces only
  `tool_result` bodies with `"[cleared to save context]"`, keeps the `tool_use` intact, and **the
  client keeps full history** (re-call the tool to restore). Params: `trigger`, `keep`,
  `clear_at_least`, `exclude_tools`, `clear_tool_inputs`. ~48% peak reduction, **no inference cost**.
- **Memory tool** (`memory_20250818`) is **recoverable/lossless-at-source**: file-backed store you
  own; the **write-before-compaction** pattern ("Extract after every turn, not at compression time")
  preserves detail across sessions. ~48% peak reduction; avoided 4 of 8 re-reads in session 2.

**Product consequence:** the "no info released" build on the Anthropic arm is
**`clear_tool_uses` + `memory`, with compaction disabled or capped**. That is literally the native,
plug-and-play expression of admission + retirement + recover — and it draws the user's exact line.

---

## 4. Updated recommendations (delta over the base survey)

1. **Add `lcm_grep` — search over the retired store.** The harness already has `recover(id)`
   (= `lcm_expand`); a regex/keyword (later semantic) search over retired results closes the loop and
   matches the LCM blueprint. Highest-value next feature for "complete + plug-and-play."
2. **On the Anthropic arm, prefer native `clear_tool_uses` + `memory`; keep `compact` off/last.**
   This is the vendor-native, lossless expression of the project's design and avoids the `/compact`
   information loss the user objects to. Stack compaction only as a last-resort overflow valve.
3. **Prototype a task-conditioned relevance signal (Squeez-style) as an *optional* retirement input.**
   Supersession retires by *staleness*; Squeez retires by *task-relevance* (a small learned pruner).
   Combine: retire on supersession OR low task-relevance — and this is exactly where a calibrated
   classifier (local, or a Jev-style API) would plug in as the gate.
4. **Local arm: Cartridges for static reused context, CacheBlend for recovery-paging.** Cartridges
   trains a 26×-smaller KV for the repo map / style guide (cold, stable context); CacheBlend gives
   near-100% KV reuse when paging retired chunks back in — both serving-stack-only, both reinforcing
   the local-arm moat.
5. **Frame the thesis as rate–distortion.** The 2607.08032 view (optimal keep/forget) and the finding
   that summaries are largely input-invariant (2605.23296) are a clean theoretical backing for
   "prefer recoverable retention over lossy summarization."

---

## 5. References (new this pass)

- LCM: Lossless Context Management — arXiv:2605.04050 (2026; vendor "Volt"; verify)
- Squeez: Task-Conditioned Tool-Output Pruning for Coding Agents — arXiv:2604.04979 (2026; verify)
- Provence: Efficient and Robust Context Pruning for RAG — arXiv:2501.16214 (ICLR 2025)
- Cartridges: long-context via self-study — arXiv:2506.06266 (HazyResearch); Cartridges at Scale — arXiv:2606.04557
- CacheBlend — ACM EuroSys 2025 (Best Paper); blog.lmcache.ai. CacheGen — KV compress/stream (LMCache)
- Anthropic cookbook — "Context engineering: memory, compaction, and tool clearing", platform.claude.com/cookbook (`compact_20260112`, `clear_tool_uses_20250919`, `memory_20250818`)
- Rate–Distortion view of memory compaction — arXiv:2607.08032 (2026; verify)
- Parallel Context Compaction for Long-Horizon LLM Agent Serving — arXiv:2605.23296 (2026; verify)
- Contextual Memory Virtualisation — arXiv:2602.22402 (2026; verify)
- Memory as Action — arXiv:2510.12635 (2025)
- Surveys: Preprints 202603.0359; arXiv:2603.07670; arXiv:2602.06052
