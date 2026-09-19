# Lossless & Recoverable Context Retention — External Research Survey

**For:** LLM Token-Efficiency for Agentic Coding
**Framing thesis (already validated by the project):** token efficiency = **admission control** (govern what enters the prompt prefix) + **lifetime control** (retire superseded tool results, but leave a **recovery hint** so nothing is permanently lost).
**Research question:** techniques for retaining/preserving context **without losing information** — the opposite of lossy summarization/compaction — surveyed for their **plug-and-play feasibility** in the two deployment arms.

**Two deployment arms (the constraint that drives everything below):**
1. **Gateway proxy** — sits between a client and a **closed** provider API (Anthropic Messages / OpenAI Chat). It sees only request/response **text** (messages, tool calls). **No access** to logits, attention scores, or the KV cache.
2. **Local-SLM arm** — an OpenAI-format agent loop against **self-hosted** vLLM/Ollama. Full control of the serving stack (KV cache, quantization, attention, embedding-level input).

**Date of survey:** 2026-09-19. Primary sources (arXiv / official docs / GitHub) preferred. Where a "lossless" claim is marketing, it is flagged.

---

## 0. Definitions used in this report

**Lossy vs lossless vs recoverable** (three states, not two):
- **Lossless** — the model provably sees information equivalent to the full original; no detail is dropped (e.g., prompt caching: identical tokens; VeriCache: identical output distribution).
- **Recoverable (lossy-but-restorable)** — detail is removed from the *active* prompt/cache, but the original is retained elsewhere (disk, memory file, client history) and can be paged back in on demand. **This is the project's own design point.** RAG, memory tools, and Anthropic context-editing live here.
- **Lossy (destructive)** — detail is dropped and cannot be recovered (classic summarization/compaction; most KV eviction as configured; hard-prompt token dropping without a kept original).

**Layer a technique operates at** (determines which arm can host it):
- `text-prompt` — manipulates the token/text stream. **Gateway-pluggable.**
- `external-memory-retrieval` — stores originals outside the window, pages in via tool/RAG. **Gateway-pluggable.**
- `soft-prompt / activation` — compresses into learned embeddings/soft tokens fed in embedding space. **Requires model + embedding-level input → local-SLM only.**
- `KV-cache` — evicts/quantizes/reconstructs the attention KV tensors during inference. **Requires serving stack → local-SLM only.**
- `model-weights` — needs training/fine-tuning of the target model.

**The load-bearing fact for arm selection:** a closed text API accepts **tokens in, tokens out**. Anything that must read attention/KV or inject embeddings/soft-prompts is **physically impossible** over that API, regardless of vendor cooperation.

---

## 1. Master comparison table

| # | Technique | Lossy / Lossless / Recoverable | Layer | Gateway-pluggable (closed text API)? | Needs local serving stack? | One-line relevance to admission + retirement |
|---|-----------|-------------------------------|-------|--------------------------------------|----------------------------|----------------------------------------------|
| **Hard-prompt (text) compression** | | | | | | |
| 1 | **LLMLingua** (2023) | Lossy (recoverable only if original kept) | text-prompt | ✅ Yes (runs a small side-LM in the proxy) | ❌ No | Drops low-perplexity tokens *before* admission; pair with kept-original recovery hint |
| 2 | **LongLLMLingua** (2023) | Lossy | text-prompt | ✅ Yes | ❌ No | Query-aware token dropping + reordering to fight "lost-in-the-middle" |
| 3 | **LLMLingua-2** (2024) | Lossy | text-prompt | ✅ Yes (BERT-size classifier, 3–6× faster) | ❌ No | Cheapest gateway-side admission filter; task-agnostic token classifier |
| 4 | **Selective Context** (2023) | Lossy | text-prompt | ✅ Yes (small causal LM for self-information) | ❌ No | Query-independent pruning of low-information spans at admission |
| **Soft-prompt / activation compression** | | | | | | |
| 5 | **Gist tokens** (2023) | Lossy (learned) | soft-prompt / activation | ❌ No | ✅ Yes (weights + attention-mask training) | Up to 26× prompt→soft-token; needs embedding-level input Claude can't accept |
| 6 | **AutoCompressor** (2023) | Lossy | soft-prompt / activation | ❌ No | ✅ Yes | Recursive summary-vector compression of long context |
| 7 | **ICAE** (In-Context Autoencoder, 2023) | Lossy (~4–15×, "near-lossless" reconstruction claimed) | soft-prompt / activation | ❌ No | ✅ Yes (frozen decoder, but embedding input) | Autoencode context into memory slots; decode with same LLM |
| 8 | **500xCompressor** (2024) | Lossy (6×–480×; quality drops with ratio) | soft-prompt / activation (compressed KV) | ❌ No | ✅ Yes | Extreme ratios via compressed-token KV; "480×" is best-case, not lossless |
| 9 | **xRAG** (2024) | Lossy | soft-prompt / activation | ❌ No | ✅ Yes (modality projector into embedding space) | One-token document injection; strictly embedding-level, un-gatewayable |
| **KV-cache eviction / sparsity** | | | | | | |
| 10 | **StreamingLLM** (attention sinks, 2023) | Lossy (middle evicted) | KV-cache | ❌ No | ✅ Yes | Keep sink + recent window for infinite streaming; drops the middle |
| 11 | **H2O** (Heavy-Hitter Oracle, 2023) | Lossy | KV-cache | ❌ No | ✅ Yes | Evict all but heavy-hitter + recent KV by attention mass |
| 12 | **Scissorhands** (2023) | Lossy | KV-cache | ❌ No | ✅ Yes | "Persistence of importance" test-time KV pruning |
| 13 | **FastGen** (Model Tells You What to Discard, 2023) | Lossy | KV-cache | ❌ No | ✅ Yes (attention profiling) | Per-head adaptive KV policy, no retraining |
| 14 | **SnapKV** (2024) | Lossy | KV-cache | ❌ No | ✅ Yes | Cluster important KV positions from prompt attention before generation |
| 15 | **PyramidKV** (2024) | Lossy | KV-cache | ❌ No | ✅ Yes | Layer-wise budget: more KV in low layers, less in high |
| 16 | **Ada-KV / CAKE / KVzip** (2024–2025) | Lossy → near-lossless (KVzip reconstructs) | KV-cache | ❌ No | ✅ Yes | Adaptive/layer-preference budgets; KVzip is query-agnostic reuse |
| **KV-cache quantization** | | | | | | |
| 17 | **KIVI** (2024) | Lossy (2-bit) but near-lossless quality | KV-cache | ❌ No | ✅ Yes (plug-and-play *into the serving stack*, not the proxy) | Asymmetric 2-bit KV quant; halves-plus KV memory |
| 18 | **GEAR / KV-Distill** (2024–2025) | Near-lossless (still lossy in the strict sense) | KV-cache | ❌ No | ✅ Yes | Marketed "near-lossless"; residual/low-rank correction of quant error |
| 19 | **VeriCache** (2025–2026) | **Lossless (provably identical output)** | KV-cache | ❌ No | ✅ Yes (needs *both* compressed and full KV) | Draft-from-compressed / verify-against-full; the one truly lossless KV method |
| **External memory, recurrence & retrieval** | | | | | | |
| 20 | **RAG** (retrieve originals from disk) | **Recoverable / lossless-at-source** | external-memory-retrieval | ✅ Yes | ❌ No | The canonical "keep originals, page in on demand" = your retirement + recovery hint |
| 21 | **MemGPT / Letta** (2023) | Recoverable | external-memory-retrieval | ✅ Yes | ❌ No | OS-style paging between window and external tiers; self-directed memory ops |
| 22 | **Mem0** (2025) | Recoverable | external-memory-retrieval | ✅ Yes | ❌ No | Production long-term memory store for agents |
| 23 | **A-Mem** (Agentic Memory, 2025) | Recoverable | external-memory-retrieval | ✅ Yes | ❌ No | Zettelkasten-style linked, self-organizing agent memory |
| 24 | **Recurrent Memory Transformer (RMT)** (2022) | Lossy (recurrent memory state) | model-weights + activation | ❌ No | ✅ Yes | Segment recurrence via memory tokens; architectural, needs training |
| **Semantic caching** | | | | | | |
| 25 | **GPTCache** (2023) | Lossy at the margin (near-miss returns approximate/stale answer) | text-prompt / external | ✅ Yes | ❌ No | Response-level dedup; avoids recompute, not a context-retention method per se |
| **Native provider features** | | | | | | |
| 26 | **Anthropic prompt caching** (2024) | **Lossless** (identical tokens) | provider-side (invoked via text API) | ✅ Yes (set `cache_control`) | ❌ No | Cuts *cost* of retained tokens, not their *count*; complements admission |
| 27 | **OpenAI prompt caching** (2024) | **Lossless** | provider-side (automatic) | ✅ Yes (automatic) | ❌ No | Automatic prefix reuse; lossless but ephemeral |
| 28 | **Gemini implicit/explicit caching** (2024–2025) | **Lossless** | provider-side | ✅ Yes | ❌ No | Explicit CachedContent gives up to 90% input discount; lossless |
| 29 | **Anthropic context editing** (`clear_tool_uses`, 2025) | **Recoverable** (placeholder left; client keeps full history) | provider-side + text-prompt | ✅ Yes (set `context_management`) | ❌ No | **Native implementation of your retirement + recovery-hint design** |
| 30 | **Anthropic memory tool** (`memory_20250818`, 2025) | **Recoverable / lossless-at-source** | external-memory-retrieval | ✅ Yes (register tool, you own the store) | ❌ No | Claude writes to your file store before clearing → lossless page-back |

Legend: ✅ = yes, ❌ = no. "Gateway-pluggable" = can be implemented in a proxy that sees only text over a closed API.

---

## 2. Per-category detail (with primary sources)

### 2.1 Hard-prompt (text) compression — *gateway-pluggable, but lossy*

These shorten the **text** itself by deleting low-information tokens. Output is natural-language tokens, so a proxy can run them and forward the result to a closed API. **All are lossy** (tokens are deleted); they become **recoverable** only if you keep the original and attach a recovery hint — exactly the project's retirement pattern.

- **LLMLingua** — Jiang et al., *LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models*, EMNLP 2023. arXiv:2310.05736. Uses a small LM (GPT-2 / LLaMA-7B) to score token perplexity and a budget controller to drop tokens; up to ~20× at the extreme. Cost: you must run the side-LM in the proxy.
- **LongLLMLingua** — Jiang et al., 2023. arXiv:2310.06839. Query-aware compression + document reordering; reports up to +21.4% on NaturalQuestions with ~4× fewer tokens on GPT-3.5-Turbo, mitigating "lost-in-the-middle."
- **LLMLingua-2** — Pan et al., *Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression*, ACL 2024 Findings. arXiv:2403.12968. Reframes compression as **token classification** with a BERT-size encoder distilled from GPT-4 labels; **3–6× faster** than LLMLingua-1 and task-agnostic → the most practical gateway-side admission filter. Repo: github.com/microsoft/LLMLingua.
- **Selective Context** — Li et al., *Compressing Context to Enhance Inference Efficiency of LLMs*, EMNLP 2023. arXiv:2304.12102. Uses a causal SLM's **self-information (entropy)** to prune low-information lexical units; query-independent.
- **Successor / survey:** Li et al., *Prompt Compression for Large Language Models: A Survey*, NAACL 2025. arXiv:2410.12388 (taxonomy of hard vs soft methods; the reference map for this space). Recent lightweight successor: *MOOSComp* (arXiv:2504.16786) improves the LLMLingua-2 classifier by mitigating over-smoothing.

**Honest note:** hard-prompt compression *does* reduce admitted tokens, but it is destructive at the text layer. For a "nothing is lost" product it must be wrapped: keep the verbatim original in the retirement store and leave a `[compressed: N tokens elided — recover with <id>]` hint.

### 2.2 Soft-prompt / activation compression — *local-SLM ONLY*

These learn to compress text into a handful of **soft tokens / memory slots / compressed KV** consumed in **embedding space**. A closed text API cannot accept embeddings or soft-prompts, so **none of these can run over the Claude/OpenAI text gateway**. They need the model weights (or at least embedding-level input to a self-hosted model) → **local-SLM arm only.**

- **Gist tokens** — Mu, Li, Goodman, *Learning to Compress Prompts with Gist Tokens*, NeurIPS 2023. arXiv:2304.08467. Trains the model (via attention-mask trick) to distill a prompt into reusable "gist" activations; up to 26×. Needs weight-level training.
- **AutoCompressor** — Chevalier et al., *Adapting Language Models to Compress Contexts*, EMNLP 2023. arXiv:2305.14788. Recursively compresses segments into summary vectors.
- **ICAE (In-Context Autoencoder)** — Ge et al., 2023. arXiv:2307.06945. Encoder (LoRA) compresses context into memory slots; **frozen** target LLM decodes. ~4–15× with "near-lossless" reconstruction *claims* — still fundamentally lossy.
- **500xCompressor** — Li et al., 2024. arXiv:2408.03094. Extends ICAE, stores compressed **KV** rather than embeddings; 1/4/16 tokens compress 96–480 tokens → **6×–480×**. The "500×"/"480×" figure is best-case; quality degrades with ratio → **not lossless**.
- **xRAG** — Cheng et al., *Extreme Context Compression for RAG with One Token*, NeurIPS 2024. arXiv:2405.13792. A modality projector maps a retrieved document to a **single** LLM-embedding token. Purely embedding-level → un-gatewayable by construction.

**Why this matters strategically:** this entire high-compression class (26×–480×) is **unavailable to the closed-API gateway** and **only unlockable on the local-SLM arm**.

### 2.3 KV-cache eviction / sparsity / quantization — *local-SLM ONLY*

These operate on the attention **KV tensors** during inference. They require attention scores and cache tensors that a closed API never exposes → **local-SLM arm only.** (Note: several call themselves "plug-and-play," meaning plug-and-play *into a serving stack like vLLM* — **not** into a text proxy.)

Eviction / sparsity (all **lossy** — evicted KV is gone):
- **StreamingLLM** — Xiao et al., ICLR 2024. arXiv:2309.17453. Identifies **attention sinks**; keep first few tokens + a rolling recent window → unbounded streaming. Middle context is dropped.
- **H2O (Heavy-Hitter Oracle)** — Zhang et al., NeurIPS 2023. arXiv:2306.14048. Dynamic eviction keeping "heavy hitter" + recent tokens by accumulated attention.
- **Scissorhands** — Liu et al., NeurIPS 2023. arXiv:2305.17118. Exploits "persistence of importance" to prune KV at test time.
- **FastGen** — Ge et al., *Model Tells You What to Discard*, ICLR 2024. arXiv:2310.01801. Per-head adaptive KV policy from lightweight attention profiling; no retraining. Repo: github.com/machilusZ/FastGen.
- **SnapKV** — Li et al., NeurIPS 2024. arXiv:2404.14469. Clusters important prompt-KV positions before generation.
- **PyramidKV** — Zhang et al., 2024. arXiv:2406.02069. Layer-wise pyramidal budget (more KV low, less high).
- **2024–2025 successors:** **Ada-KV** (adaptive budget allocation, arXiv:2407.11550); **CAKE** (cascading/layer-preference eviction, arXiv:2503.12491); **KVzip** (NeurIPS 2025) — query-agnostic compression **with context reconstruction**, 3–4× smaller KV, ~2× faster decode, *near-lossless* across QA/retrieval/reasoning/code. NVIDIA **kvpress** (github.com/NVIDIA/kvpress) packages many of these for one serving stack.

Quantization (**lossy but near-lossless quality**):
- **KIVI** — Liu et al., ICML 2024. arXiv:2402.02750. Tuning-free **asymmetric 2-bit** KV quant: key cache per-channel, value cache per-token. "Plug-and-play" *inside the serving stack.*
- **GEAR** and **KV-Distill** — 2024–2025, marketed "**near-lossless**"; GEAR adds low-rank + residual correction of quantization error, KV-Distill learns a nearly-lossless learnable compressor. Honest read: **near-lossless ≠ lossless** — output can diverge on adversarial/long-tail inputs.

The genuinely lossless KV method (with a caveat):
- **VeriCache** — 2025–2026 (arXiv:2605.17613, per search index). **Turns lossy KV compression into lossless inference**: draft tokens from the *compressed* cache, **verify against the full cache** (speculative-decoding style), yielding **provably identical output** at up to ~4× throughput. Caveat: it must **keep the full KV** alongside the compressed one, so it saves *compute/latency*, not *memory footprint* — and it is still serving-stack-only.

### 2.4 External memory, recurrence & retrieval — *mostly gateway-pluggable, recoverable/lossless-at-source*

This is the family that matches the project's design most directly: **keep originals in an external store, page in on demand.** These operate at the text/orchestration layer, so a proxy can host them over a closed API. They are **recoverable** (lossless *at source*); the only loss is at **retrieval** (recall < 100%), which is a tuning problem, not destruction.

- **RAG (retrieval-augmented generation)** — the canonical lossless-retention strategy: originals live on disk; only relevant chunks enter the prompt; everything else is retired but retrievable. **This *is* the "retirement + recovery hint" pattern** — the recovery hint is the retrieval key/id.
- **MemGPT** — Packer et al., *MemGPT: Towards LLMs as Operating Systems*, 2023. arXiv:2310.08560. OS-style **virtual context management**: hierarchical memory tiers, self-directed paging in/out of the window via function calls, interrupt-driven control. Now productized as **Letta** (filesystem + PostgreSQL persistence). Fully realizable in a gateway (it's tool-call orchestration).
- **Mem0** — Chhikara et al., 2025. arXiv:2504.19413. Production-ready scalable long-term memory layer for agents.
- **A-Mem (Agentic Memory)** — Xu et al., 2025. arXiv:2502.12110. Self-organizing, linked (Zettelkasten-style) memory notes that evolve over time.
- **Recurrent Memory Transformer (RMT)** — Bulatov et al., NeurIPS 2022. arXiv:2207.06881. Segment-level recurrence via special **memory tokens** carried across segments. **Architectural** (needs training/weight access) → local-SLM only, and the recurrent state is a **lossy** summary. Included for completeness; not a drop-in retention layer.

### 2.5 Semantic caching — *gateway-pluggable, tangential*

- **GPTCache** — Bang, *GPTCache: An Open-Source Semantic Cache for LLM Applications*, NLP-OSS @ EMNLP 2023 (github.com/zilliztech/GPTCache). Embeds queries; on a semantic near-match, returns a **cached response** instead of recomputing. This is **cost/latency dedup**, not context retention — and it is **lossy at the margin**: a near-miss can return an approximate or stale answer. Useful as a request-dedup layer in front of the gateway; low relevance to the admission/retirement core. Newer variants (ContextCache, multi-turn semantic caches) refine match precision.

### 2.6 Native provider features — *gateway can invoke them; most are lossless or recoverable*

Prompt caching (all three vendors) is **lossless** — the model still sees the **identical** tokens; caching only discounts the **cost/latency** of re-processing a repeated prefix. **Critical distinction:** caching reduces the *price* of retained tokens, **not their count**. It is **complementary** to admission control (which reduces count), never a substitute.

- **Anthropic prompt caching** (2024) — explicit `cache_control` breakpoints (up to 4); min cacheable prefix ~1024 tokens (Sonnet/Opus) / ~2048 (Haiku); default 5-min TTL, optional 1-hour; cache **write** ~1.25× (5m) / ~2× (1h) input price, cache **read** ~0.1×. Lossless.
- **OpenAI prompt caching** (2024) — **automatic** for prompts ≥ ~1024 tokens; longest matching prefix served from cache at a discount (historically ~50%; newer families add a small write fee). No markers, no opt-in. Lossless.
- **Gemini caching** (2024–2025) — **implicit** (on by default for 2.5, automatic prefix hashing) and **explicit** (`CachedContent` API with TTL; **up to 90%** input discount on 2.5, 75% on 2.0; storage billed by duration). Lossless.

Context lifetime management (the direct analogues of the project's thesis):
- **Anthropic context editing** — public beta, header `anthropic-beta: context-management-2025-06-27`, strategy `clear_tool_uses_20250919` (+ `clear_thinking_20251015`). **Server-side** clearing of the **oldest tool results** (optionally tool inputs, and thinking) when a `trigger` (default 100k input tokens) is crossed, keeping the last `keep` tool uses (default 3), with `clear_at_least` to avoid churning the cache and `exclude_tools` to protect specific tools. **Two properties that map exactly onto your design:**
  1. **Recovery hint is native:** "The API replaces each cleared result with **placeholder text** indicating to Claude that it was removed" — i.e., retirement leaves a marker, not a hole.
  2. **Recoverable by construction:** "Context editing is applied **server-side**; your client application maintains the **full, unmodified conversation history**." The original is never destroyed at the client/proxy.
  Reported impact: **84% token reduction** on a 100-turn web-search eval; **+29%** performance from context editing alone. Interaction with caching is honest in the docs: clearing **invalidates the cached prefix** from the clear point, which is why `clear_at_least` exists. Docs: platform.claude.com/docs/en/build-with-claude/context-editing; announcement: claude.com/blog/context-management.
- **Anthropic memory tool** — `memory_20250818`. File-based CRUD in a memory directory **stored in *your* infrastructure** (you own the backend) → **lossless at source**. Pairs with context editing: when the trigger nears, "Claude receives an automatic warning to preserve important information" and can **write tool results to memory before they're cleared**, then read them back later. Combined memory + context editing: **+39%** over baseline. This is the vendor's own version of retire-with-recovery.

---

## 3. Synthesis — what is plug-and-play for the CLOSED-API gateway vs what needs the local-SLM serving stack

**The thesis under test:** *KV-cache methods and soft-prompt compression need serving-stack access, so they are ONLY available on the local-SLM arm, not the Claude gateway — which makes the local-SLM arm strategically valuable.*

**Verdict: CONFIRMED, with one refinement about the middle tier.**

**A. Physically impossible over a closed text API (local-SLM ONLY):**
- **All KV-cache methods** (StreamingLLM, H2O, Scissorhands, FastGen, SnapKV, PyramidKV, Ada-KV, CAKE, KVzip, KIVI, GEAR, KV-Distill, VeriCache). They read/rewrite attention KV tensors. The API exposes neither. Their own "plug-and-play" branding means *into vLLM/TensorRT-LLM*, not into a proxy. **✔ thesis holds.**
- **All soft-prompt / activation methods** (Gist, AutoCompressor, ICAE, 500xCompressor, xRAG). They feed **embeddings / soft tokens / compressed KV** — the Messages/Chat API accepts only **text tokens**. You cannot hand Claude a gist vector or an xRAG document-token. **✔ thesis holds.**
- **RMT** — architectural recurrence, needs weight-level training. Local-SLM only.

This is the strategic payoff: the local-SLM arm unlocks an **entire class of techniques the gateway can never host** — and it is precisely the **highest-compression** class (soft-prompt 26×–480×; KV 2-bit quant; heavy-hitter eviction) **and** the only place a **provably lossless** compute method (VeriCache) can live. On the closed API you are capped at what you can do to text; on your own stack you can also act on activations and cache. That asymmetry is the local-SLM arm's moat.

**B. Fully plug-and-play in the closed-API gateway (text/orchestration layer):**
- **External memory & retrieval** — RAG, MemGPT/Letta, Mem0, A-Mem. All are tool-call/orchestration patterns → proxy-hostable, and **recoverable/lossless-at-source**. These are the natural home of admission + retirement.
- **Native provider features** — prompt caching (Anthropic/OpenAI/Gemini, **lossless**), Anthropic **context editing** (**recoverable**, native retire-with-placeholder) and **memory tool** (**recoverable**, you own the store). The gateway doesn't implement these; it **invokes** them via request flags — a big win, because Anthropic context editing is literally a server-side implementation of the project's retirement+recovery-hint design, and it preserves the prompt cache better than client-side stripping.
- **Semantic caching** (GPTCache) — proxy-hostable request dedup; tangential and lossy-at-the-margin.

**C. The refinement — a *lossy* middle tier that is gateway-pluggable:**
Hard-prompt compression (**LLMLingua / LLMLingua-2 / LongLLMLingua / Selective Context**) *can* run in the gateway (it emits text), so it is **not** local-SLM-exclusive. But it is **destructive at the text layer** and adds a **side-model dependency** to the proxy (a BERT/GPT-2-class scorer). So the clean split is:
- **Lossless/recoverable + gateway:** retrieval, memory tools, native caching, native context editing.
- **Lossy + gateway (needs recovery-hint wrapper):** LLMLingua family, Selective Context, semantic caching.
- **Lossy→near/provably-lossless + local-SLM only:** all KV-cache + all soft-prompt methods.

**One myth to flag:** "prompt caching = context efficiency." It is **lossless** and valuable, but it reduces the **cost** of tokens already in context, not the **number** of tokens. It cannot substitute for admission control; it multiplies its value (cheap to keep a well-admitted prefix warm).

---

## 4. Concrete recommendations (complement admission + retirement, no lossy dropping)

Split by path. All chosen to **preserve information** (lossless or recoverable), never to summarize-and-drop.

### Gateway (closed-API) path
1. **Adopt Anthropic context editing as the native retirement engine (Anthropic arm).** It *is* your design, server-side: `clear_tool_uses_20250919` retires the oldest tool results, leaves a **placeholder recovery hint**, keeps the client's full history, and — via `clear_at_least` — protects the prompt cache. Set `trigger`, `keep`, and `exclude_tools` to match your admission policy. Reported 84% token cut / +29% quality is the benchmark to beat or match. (Lossless-to-client, recoverable.)
2. **Back retirement with the Anthropic memory tool (or your own RAG store) as the recovery layer.** Have the agent write full tool results to a disk/object store keyed by id **before** they are retired; the recovery hint carries that id; a `recover(id)` tool pages the verbatim original back in. This makes retirement **provably lossless at source** and is the vendor-blessed pattern (memory + context editing = +39%). Works identically for OpenAI/Gemini arms where you own the store (MemGPT/Letta/Mem0 as the engine).
3. **Keep native prompt caching on for the admitted prefix (all vendors).** Lossless, orthogonal to admission. Structure the prompt so the stable, well-admitted prefix is cacheable (Anthropic `cache_control` breakpoints; OpenAI automatic; Gemini explicit `CachedContent` for the system/tool preamble). This lowers the cost of *retaining* context without dropping any.
4. **Offer LLMLingua-2 as an *optional, wrapped* admission filter — off by default, never silent.** Because it is lossy, only apply it to material you simultaneously persist to the recovery store, and annotate the elision with a hint. Prefer LLMLingua-2 (BERT-size, 3–6× faster, task-agnostic) over LLMLingua-1 for proxy latency. Treat it as "compress-with-receipt," not "compact."

### Local-SLM (self-hosted vLLM/Ollama) path
5. **Turn on KV-cache quantization (KIVI 2-bit) in the serving stack.** Near-lossless quality, ~2–4× KV-memory headroom, tuning-free, no proxy changes. This is a pure local-arm win the gateway can never offer, and it lengthens the retention budget before retirement is even needed.
6. **Add query-agnostic KV compression with reconstruction (KVzip) for reused long prefixes,** and evaluate **VeriCache** where correctness must be provably identical: VeriCache is the only surveyed method that is **lossless by construction** (draft-from-compressed / verify-against-full). Use it to advertise a genuine "lossless fast path" on the local arm — with the honest caveat that it trades memory (keeps full + compressed KV) for lossless throughput, whereas KIVI/KVzip trade a little quality for memory.
7. **(Optional, R&D) Prototype soft-prompt compression (ICAE / 500xCompressor) for static, reused context** (system prompt, coding style guide, repo map) on the local SLM. 6×–480× on *cold, stable* context is unreachable on the closed API and is the clearest demonstration of the local arm's ceiling. Keep it lossy-but-versioned: store the original text so any slot can be re-expanded.

**Positioning line for the paper:** the closed-API gateway can be **complete and lossless/recoverable** using retrieval + memory + native context editing + caching; the local-SLM arm is **strictly more capable**, because it *additionally* commands the KV-cache and activation layers — home to the highest-ratio compression and the only *provably lossless* compute method (VeriCache). Admission + retirement is the shared spine; the local arm simply has more organs.

---

## 5. References (primary sources)

**Hard-prompt compression**
- LLMLingua — arXiv:2310.05736 (EMNLP 2023). github.com/microsoft/LLMLingua
- LongLLMLingua — arXiv:2310.06839
- LLMLingua-2 — arXiv:2403.12968 (ACL 2024 Findings)
- Selective Context — arXiv:2304.12102 (EMNLP 2023)
- Survey: *Prompt Compression for LLMs* — arXiv:2410.12388 (NAACL 2025); MOOSComp — arXiv:2504.16786

**Soft-prompt / activation**
- Gist tokens — arXiv:2304.08467 (NeurIPS 2023)
- AutoCompressor — arXiv:2305.14788 (EMNLP 2023)
- ICAE — arXiv:2307.06945
- 500xCompressor — arXiv:2408.03094
- xRAG — arXiv:2405.13792 (NeurIPS 2024)

**KV-cache eviction / sparsity / quantization**
- StreamingLLM — arXiv:2309.17453 (ICLR 2024)
- H2O — arXiv:2306.14048 (NeurIPS 2023)
- Scissorhands — arXiv:2305.17118 (NeurIPS 2023)
- FastGen (*Model Tells You What to Discard*) — arXiv:2310.01801 (ICLR 2024)
- SnapKV — arXiv:2404.14469 (NeurIPS 2024)
- PyramidKV — arXiv:2406.02069
- Ada-KV — arXiv:2407.11550; CAKE — arXiv:2503.12491; KVzip — NeurIPS 2025 (neurips.cc/virtual/2025/poster/118741)
- KIVI — arXiv:2402.02750 (ICML 2024)
- VeriCache (*Turning Lossy KV Cache into Lossless Inference*) — arXiv:2605.17613 (per 2026 search index; verify on publication)
- NVIDIA kvpress — github.com/NVIDIA/kvpress

**External memory / recurrence / retrieval**
- MemGPT — arXiv:2310.08560; Letta — docs.letta.com
- Mem0 — arXiv:2504.19413
- A-Mem — arXiv:2502.12110
- Recurrent Memory Transformer — arXiv:2207.06881 (NeurIPS 2022)

**Semantic caching**
- GPTCache — NLP-OSS @ EMNLP 2023; github.com/zilliztech/GPTCache

**Native provider features**
- Anthropic context editing — platform.claude.com/docs/en/build-with-claude/context-editing; announcement claude.com/blog/context-management (beta `context-management-2025-06-27`)
- Anthropic memory tool — platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool (`memory_20250818`)
- Anthropic prompt caching — platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI prompt caching — platform.openai.com/docs/guides/prompt-caching
- Gemini context caching — ai.google.dev/gemini-api/docs/caching (implicit + explicit)

---

*Honesty flags carried through this report:* (a) hard-prompt and soft-prompt compression are **lossy** — "near-lossless" (ICAE, GEAR, KV-Distill, KVzip) means *empirically close*, not guaranteed; (b) the only **provably lossless** compression-adjacent method surveyed is **VeriCache**, and it saves compute, not memory; (c) **prompt caching is lossless but is not context reduction** — it discounts retained tokens, it does not remove them; (d) retrieval/memory are **lossless at source but lossy at recall** — retrieval quality is the real risk, not storage.
