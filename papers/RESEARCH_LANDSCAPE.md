# Research landscape: LLM context and token optimization

This map covers the main research lines relevant to long-horizon coding-agent context optimization through September 2026. It distinguishes *what is compressed*, *where the mechanism runs*, and *what denominator is evaluated*. Those distinctions explain why many strong prior results are complementary rather than interchangeable.

## 1. Prompt and retrieved-context compression

| Work | What it introduced | Primary evaluation target | What remains for coding-agent runtimes |
|---|---|---|---|
| [LLMLingua (EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.825/) | Coarse-to-fine, model-guided removal of less informative prompt tokens. | Per-prompt compression, task performance, inference acceleration. | Does not decide the lifetime of objects across an interactive trajectory or price cache invalidation. |
| [LongLLMLingua (ACL 2024)](https://aclanthology.org/2024.acl-long.91/) | Query-aware compression and reordering for long documents and position bias. | Long-context QA and retrieval settings. | A changing query can rewrite an otherwise reusable prefix; session-level economics are outside its scope. |
| [LLMLingua-2 (Findings of ACL 2024)](https://aclanthology.org/2024.findings-acl.57/) | A distilled token classifier for fast, task-agnostic, extractive compression. | Faithfulness, transfer, latency, and compression ratio. | Supplies a possible compression operator, but not admission, retirement, or cache scheduling. |
| [RECOMP (ICLR 2024)](https://proceedings.iclr.cc/paper_files/paper/2024/hash/bda88ed2892f5e61c9a9bf215c566913-Abstract-Conference.html) | Extractive and abstractive compression of retrieved passages, including selective non-augmentation. | Retrieval-augmented QA. | Retrieval evidence is only one object class in a coding-agent prefix. |
| [Gist Tokens (NeurIPS 2023)](https://proceedings.neurips.cc/paper_files/paper/2023/hash/3d77c6dcc7f143aa2154e7f4d5e22d68-Abstract-Conference.html) | Learned virtual tokens that compress prompts during model training. | Instruction compression and generalization. | Requires model access/training and does not manage black-box API cache state. |
| [ICAE (ICLR 2024)](https://proceedings.iclr.cc/paper_files/paper/2024/hash/0b276510ec2d3f6613a8b60c41ff0438-Abstract-Conference.html) | An in-context autoencoder that maps long context into compact memory slots. | Reconstruction and downstream task performance. | Latent compression is not directly deployable through a text-only proprietary API. |
| [Prompt Compression Survey (NAACL 2025)](https://aclanthology.org/2025.naacl-long.368/) | Taxonomy spanning hard prompts, soft prompts, and retrieval/context compression. | Cross-method synthesis. | Highlights method diversity, but not the token-turn and prompt-cache accounting needed for agents. |

**Lesson for this project:** compression ratio is an operator-level metric. A coding-agent intervention also needs an object-selection policy, a remaining-lifetime estimate, an end-to-end session denominator, a quality check, and a cache-cost model.

## 2. Long-context use, memory, and active compaction

| Work | What it introduced | Primary evaluation target | Relation to ContextRuntime |
|---|---|---|---|
| [Lost in the Middle (TACL 2024)](https://aclanthology.org/2024.tacl-1.9/) | Evidence that models often use information less reliably when it appears in the middle of long inputs. | Retrieval/use accuracy by position. | Motivates selective context, but does not measure repeated API residency or dollar cost. |
| [RULER (2024)](https://arxiv.org/abs/2404.06654) | A multi-task diagnostic for effective rather than advertised context length. | Long-context model capability. | Measures whether a model can use context, not whether an agent should keep paying to resend it. |
| [MemGPT (2023)](https://arxiv.org/abs/2310.08560) | Virtual-memory-inspired tiers and explicit movement between in-context and external memory. | Long conversations and document analysis. | Closest conceptual ancestor for object lifetime; ContextRuntime adds client-side token accounting and cache-aligned mutation. |
| [Context as a Tool / CAT (Findings of ACL 2026)](https://aclanthology.org/2026.findings-acl.1032/) | Gives a software agent tools for maintaining a bounded context workspace. | Long-horizon SWE-agent quality under context constraints. | Shows agent-controlled maintenance can work; cache economics and object-level API billing are separate. |
| [Active Context Compression (2026)](https://arxiv.org/abs/2601.07190) | Explicit checkpoint and consolidation actions for software agents. | Token use and task success on a small coding set. | Supports active lifecycle control; this project focuses on transparent gateway policies and provider-cache timing. |
| [AutoCompact (2026 project report)](https://autocompact.github.io/) | Learns when a long-horizon coding agent should compact. | Coding-agent efficiency and task outcomes. | Learns timing from trajectories; ContextRuntime derives a pricing-aware break-even gate, but the shipped gate cannot fire on the Anthropic profiles, so its live "holding" is structural, not a validated decision. |
| [StateComp (arXiv 2609.27298, 2026)](https://arxiv.org/abs/2609.27298) | A trained router that decides when history can be compressed. | 52.27% fewer total tokens on WorkBuddyBench with performance preserved; no pricing or caching. | Competes on *when* to mutate; ContextRuntime's gate prices the decision against cache economics instead of token count. |
| [An Empirical Study of Harness Design for Coding Agents (arXiv 2609.20804, 2026)](https://arxiv.org/abs/2609.20804) | 176 matched settings across five context-management strategies and four window budgets on SWE-bench Verified and Terminal-Bench. | Rule-based elision then summarization is most efficient; costs at OpenRouter rates without prompt-cache pricing. | Rule-based elision is established at a scale far beyond our 3–4 tasks; its cost ranking omits the cache pricing that ContextRuntime measures. |
| [TRACE (arXiv 2608.06503, 2026)](https://arxiv.org/abs/2608.06503) | Shows recurrent compression increases execution instability; verifier-guided optimization of the compaction prompt. | Reliability of compaction; no cost accounting. | Supports holding history by default and mutating only for a priced reason. |
| [VERA (arXiv 2608.29897, 2026)](https://arxiv.org/abs/2608.29897) | Context management for long-horizon agents with multimodal history. | 31.5–63.1% fewer cumulative non-cache tokens, which the authors note is not monetary cost. | Independent support for the token ≠ dollar thesis that B6 measures live. |
| [Agent-memory survey (TOIS 2025)](https://doi.org/10.1145/3748302) | Organizes memory forms, operations, and evaluation for LLM agents. | Field taxonomy. | ContextRuntime instantiates a narrow memory operation—safe replacement—with explicit occupancy and billing ledgers. |

**Lesson for this project:** memory systems answer *what should remain semantically available*. A production runtime must additionally decide *what remains literally in the billable prefix* and *when changing that prefix is worth a cache rewrite*.

## 3. Repository retrieval and software-agent interfaces

| Work | What it introduced | Primary evaluation target | Why it did not settle this project |
|---|---|---|---|
| [SWE-bench (ICLR 2024)](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html) | Real repository issues with executable patch evaluation. | Issue-resolution accuracy. | Provides the task and grading substrate, not a context-efficiency method. |
| [SWE-agent (NeurIPS 2024)](https://arxiv.org/abs/2405.15793) | A purpose-built agent-computer interface for repository navigation and editing. | Resolved issues and agent behavior. | Interface design affects turns and context, but is not itself an object-lifetime or cost scheduler. |
| [Agentless (FSE 2025)](https://arxiv.org/abs/2407.01489) | A simplified localization–repair–validation pipeline without a general autonomous agent loop. | Issue resolution with a structured pipeline. | Demonstrates that fewer interaction modes can be effective; it changes the agent architecture rather than optimizing an existing session. |
| [RepoCoder (EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.151/) | Iterative retrieval and generation for repository-level completion. | Code completion. | Better retrieval can lower irrelevant context, but its accuracy target differs from end-to-end agent token cost. |
| [GraphCoder (ASE 2024)](https://arxiv.org/abs/2406.07003) | Coarse-to-fine retrieval over a code-context graph. | Repository-level completion. | Motivated this repository's graph experiments; the local G1/G2 line failed its joint recall–budget gate. |
| [LocAgent (ACL 2025)](https://aclanthology.org/2025.acl-long.426/) | Graph-guided agentic code localization. | File/symbol localization and downstream repair. | Strong localization does not guarantee lower cumulative input once graph construction and discovery turns enter the denominator. |

**Lesson for this project:** retrieval quality is not token efficiency by definition. In the repository's own experiments, retrospective graph ceilings looked promising, but prospective bundles missed the recall–budget gate and enforced eager discovery increased pooled live input by 55.6%.

## 4. Tool discovery and schema admission

| Work | What it introduced | Primary evaluation target | Relation to ContextRuntime |
|---|---|---|---|
| [Re-Invoke (Findings of EMNLP 2024)](https://aclanthology.org/2024.findings-emnlp.270/) | Rewrites an invocation into a retrieval query for zero-shot tool selection. | Tool-retrieval recall and downstream invocation. | Supports on-demand discovery; ContextRuntime's admission layer targets the fixed schema bytes sent on every turn. |
| [ToolRerank (LREC-COLING 2024)](https://aclanthology.org/2024.lrec-main.1413/) | Adaptive hierarchy-aware reranking of tools. | Tool retrieval. | Complements schema deferral, but does not account for repeated schema residency. |
| [MCP-Zero (2025)](https://arxiv.org/abs/2506.01056) | Proactive construction of toolchains from a large tool ecosystem. | Tool selection and composition. | Addresses which tools to assemble; ContextRuntime measures the cost of exposing their schemas and enforces a minimal admitted set. |
| [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | Provider features for tool search and programmatic tool calling. | Tool accuracy and context reduction in the Claude platform. | Confirms admission is becoming a platform primitive; gateway deployments must verify whether client-native deferral remains enabled. |
| [Anthropic tool search tool (`defer_loading`)](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) | Deferred tool definitions stay out of the context and system-prompt prefix and load on demand. | Typically >85% reduction for a ~55k-token multi-server setup; the prefix is untouched, so caching is preserved. | Admission is a first-party mechanism. The open comparison—admission versus native deferral/tool search—has not been run. |
| [Tool Attention (arXiv 2604.21816, 2026)](https://arxiv.org/abs/2604.21816) | Dynamic tool gating and lazy schema loading. | 95% fewer per-turn tool tokens on a simulated 120-tool benchmark; end-to-end gains projected, not measured. | Same mechanism family as admission; ContextRuntime's distinct part is the live, cache-priced measurement. |

**Lesson for this project:** tool retrieval chooses capabilities, while admission control determines whether thousands of schema tokens become a fixed prefix. Admission is now a crowded mechanism (first-party tool search, Claude Code's own MCP deferral, Tool Attention); this project claims no priority on it. The evaluated configuration launched the client with `--disallowedTools` for 25 built-in tools and three MCP servers (personal and company connectors); the gateway performs no admission. Its "82 schemas to 6" count was captured through the proxy, where the client's MCP deferral is off; against the stock deferring client the measured saving is 23,424 tokens per request (41,554 → 18,130), and its size depends on how many unused tools the environment exposes.

## 5. Serving systems and prefix/KV reuse

| Work | What it introduced | Primary evaluation target | Client-side gap |
|---|---|---|---|
| [vLLM / PagedAttention (SOSP 2023)](https://arxiv.org/abs/2309.06180) | Paged KV-cache memory management for high-throughput serving. | Throughput and GPU memory efficiency. | The server owns KV allocation; an API client still controls prompt content and mutation. |
| [SGLang (2024)](https://arxiv.org/abs/2312.07104) | RadixAttention and a runtime for structured language-model programs. | Serving throughput and reuse across program executions. | Optimizes reusable prefixes on the server side, not agent-object safety or API tariffs. |
| [Prompt Cache (MLSys 2024)](https://proceedings.mlsys.org/paper_files/paper/2024/hash/a66caa1703fe34705a4368c3014c1966-Abstract-Conference.html) | Modular reuse of attention state across prompts. | Latency and computation reuse. | Assumes serving-layer control unavailable to a black-box client. |
| [ChunkAttention (ACL 2024)](https://aclanthology.org/2024.acl-long.623/) | Prefix-aware attention sharing with two-phase partitioning. | Memory and throughput. | Does not decide whether an application rewrite is economically beneficial. |
| [RAGCache (2024)](https://arxiv.org/abs/2404.12457) | Caches intermediate states for retrieved knowledge. | RAG latency and throughput. | Targets reusable retrieved documents rather than mutable agent histories. |
| [CacheBlend (EuroSys 2025)](https://arxiv.org/abs/2405.16444) | Selective recomputation to fuse cached knowledge chunks. | RAG time-to-first-token. | Requires inference-stack access. |
| [Preble (2024)](https://arxiv.org/abs/2407.00023) | Distributed scheduling that exploits prompt sharing. | Cluster load, latency, and throughput. | Server scheduling cannot label a tool result as semantically superseded. |
| [Parrot (OSDI 2024)](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan) | A serving layer with semantic variables and dataflow-aware scheduling. | Application-wide serving efficiency. | Rich server/runtime co-design; ContextRuntime instead works at a provider-agnostic client accounting boundary, with one adapter exercised live. |

**Lesson for this project:** serving research proves that prefix reuse is valuable. ContextRuntime asks the dual client question: when should an application preserve a byte-stable cached prefix, and when do enough future reads remain to justify breaking it?

## 6. Prompt-cache economics

| Work | What it introduced | Primary evaluation target | Distinction in this repository |
|---|---|---|---|
| [Don't Break the Cache (2026)](https://arxiv.org/abs/2601.06007) | Empirical evaluation of prompt caching in long-horizon agent tasks. | Cost/latency sensitivity to cache-friendly prompt structure. | Closely aligned motivation; ContextRuntime adds object-level safe retirement and live token-versus-dollar measurements on an unmodified client (B6 preregistered; B8 v2's model prediction was committed with its results, so it is a post-hoc check). |
| [Cache-Aware Prompt Compression (2026)](https://arxiv.org/abs/2607.15516) | A two-tier economic model for deciding compression under API cache prices. | Dollar break-even behavior. | ContextRuntime independently operationalizes the same central tradeoff in an agent gateway and exposes the decision in logs; as shipped, its break-even branch cannot fire on the Anthropic profiles. |
| [Keeping the Cache Warm Pays (2026)](https://arxiv.org/abs/2607.19214) | Economic analysis of keepalive requests for agentic workloads. | Cache retention economics. | Optimizes whether to preserve cache state over idle gaps; ContextRuntime optimizes whether to mutate the content inside an active session. |
| [TokenPilot (arXiv 2606.17016, 2026)](https://arxiv.org/abs/2606.17016) | Gateway-level, cache-efficient context management for LLM agents: keeps a stable prefix and evicts by lifecycle, priced against cache hits and misses. | 61%/56% (isolated) and 61%/87% (continuous) cost reductions on PinchBench and Claw-Eval (abstract). | **Closest prior work.** No explicit break-even rule; ContextRuntime adds a live token-versus-dollar measurement on an unmodified commercial client, admission by removal, and an explicit priced mutation inequality. |
| [Anthropic context editing (`clear_tool_uses_20250919`)](https://platform.claude.com/docs/en/build-with-claude/context-editing) | Replaces older tool results with placeholders after a token trigger (default 100k), keeping the most recent N (default 3); `clear_at_least` sets a minimum clearing size because clearing invalidates cached prefixes. | Product semantics. | Forward-only tool-result retirement is first-party. ContextRuntime's scheduler is a priced, dynamic generalization of the static `clear_at_least` threshold. |
| [Anthropic prompt caching documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Published read/write multipliers, breakpoints, and TTL behavior for a commercial API. | Product semantics. | Supplies price parameters, but live traces are still needed for partial-hit and client-integration behavior. |

## The research gap and the two-paper story

The remaining gap is not “compress context better” in the abstract. It is a client-side control problem with five coupled decisions:

1. **Account:** reconstruct each real request and separate cumulative input workload from provider-priced cost.
2. **Admit:** keep unused fixed schemas and instructions out before they become recurrent prefix.
3. **Retire:** replace only objects with a prospective safety rule and a recovery handle.
4. **Schedule:** preserve byte stability until expected future discounted reads repay the cache-write penalty.
5. **Validate:** report task quality, workload, dollars, and mutation safety separately, with live and modeled claims visibly labeled.

The measurement paper owns decisions 1 and 5 and explains why naive approaches failed. The systems paper owns decisions 2–4 and shows a live result that is more nuanced than “always compact”: 41.5% lower cumulative input under the full unaligned stack, but only 2.5% lower client-reported cost, with an admission-only counterfactual that predicts at least that input reduction; then 29.34% lower live list-price cost in B8 v2, which comes from admission (a client launch flag) because the gateway applied no mutation—the shipped scheduler's break-even branch cannot fire on the Anthropic profiles, so its holding was structural. B8 v2's matching 29.5% model figure was committed together with its results, so it is a post-hoc check, not a preregistered validation.

## Defensible novelty statement

> Prior work compresses prompt text, manages semantic memory, retrieves code or tools, reuses prefixes in the serving stack, and—in TokenPilot and Anthropic's context editing—already manages agent context with cache costs in view. ContextRuntime's defensible contributions are narrower:
>
> 1. **Live evidence on an unmodified commercial client that token reductions need not become dollar reductions under prompt caching.** In B6, a preregistered live A/B on Claude Code, the treatment cut cumulative input tokens 41.5% but client-reported cost only 2.5%, because unaligned history edits raised cache writes 68.1%.
> 2. **An explicit priced inequality for history mutation, with a calibrated cache-cost model.** The gate weighs expected discounted future reads against the suffix rewrite cost, a priced, dynamic generalization of `clear_at_least` and TokenPilot-style lifecycle gating. The model is exact on 11 of 12 append-only native sessions; its edit branch has no committed calibration. As shipped, the break-even branch cannot fire on the Anthropic profiles, so this contribution is the rule and the model, not a live demonstration of the rule firing.
> 3. **Black-box deployment with object-level ledgers and honest negative results.** The runtime sits at the client boundary of an unmodified client, keeps occupancy and price ledgers per object, labels live, modeled, and retrospective claims separately, and reports the failed mechanisms alongside the surviving ones.
>
> Not claimed: priority on admission (crowded: first-party tool search, Claude Code's own MCP deferral, Tool Attention), on forward-only retirement (first-party context editing; rule-based elision in the harness-design study), or on preregistered live-dollar validation (B8 v2's prediction was not preregistered).

This is a positioning claim, not a priority claim. The cache-aware literature is moving quickly; independent replication and a continuously maintained related-work search remain necessary.
