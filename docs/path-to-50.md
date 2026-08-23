# Where the tokens actually go — and what a 50% reduction would take

**2026-08-23. Zero new model quota** (one OBSERVE proxy session, ~$1.20, already run). Measurements:
`corpus/prefix_decomposition_v2.py` → `corpus/analysis/prefix-decomposition-v2.json`; corrected
B3 artifacts; external sources at the end. This answers "what else can be done — target 50%?" with
our own data first, then the outside evidence, then an honest lever stack.

## 0. A correction that had to come first (transcript turn counting)

Claude Code stores **one API call as several assistant records** (thinking / text / tool_use split
across lines), each repeating the same `usage`. Every B3 harness counted records as turns. Verified on
django-10554: **134 records = 71 real API calls; Σ usage 10.9M vs the CLI-reported 5.69M.** Fixed at the
source (`corpus/transcript_util.merged_records`, requestId-merged); the harness now reproduces the CLI
exactly (71 / 5,690,119 — match).

**The B3 headline survives** — numerator and denominator inflated together:

| | documented | corrected (real calls) |
|---|---|---|
| B3.0 pooled mech / +tail | 4.3% / 10.3% | **4.33% / 10.32%** |
| B3.1 lag-5 safe_fraction / safeNET | 0.974 / 8.70% | **0.979 / 8.30%** |
| length buckets | 0–60 / 60–100 / 100–150 / 150+ "turns" | real sessions are **20–180 API calls**: 0–60 (n=44): 3.9 / 9.0 · **60+ (n=16): 5.6 / 13.9** (mech / +tail) |

So restate the frozen headline's length clause as "≥60 real API calls: ~14% tail / ~5.6% provably-safe
/ 9.3% cost-NET" rather than "≥100 turns: 11.1%". The growth-with-length trend holds.

## 1. The full decomposition — where Σ P_t goes (60 django sessions, mean 49.5 real calls, 42k startup)

Everything previously measured was the tool-OUTPUT slice. This is the whole resident prefix, weighted
by token-turns (share of Σ P_t ≈ T_total). The system prompt is invisible in transcripts, so it is the
residual `P_t − visible_t`; its **hard lower bound** (startup prefix × calls / Σ P_t) is 66.9%.

| component | share of Σ P_t | notes |
|---|---:|---|
| **fixed: system prompt + tool defs + injected (CLAUDE.md, skills, memory, reminders)** | **73.0%** | lower bound 66.9%; in this machine's heavy-MCP env a 7-call session was **98.9%** (82k startup) |
| **retained thinking** (stored empty, but resident on Opus 4.5+/Sonnet 4.6+) | **11.3%** | estimated as `output_tokens − visible output` per call |
| tool results: Read 6.8 · Bash 4.9 · Edit 0.35 · other 0.04 | **12.0%** | the slice B1/B2/B3 worked on |
| tool_use inputs: Bash 1.2 · Edit 1.0 · other 0.5 · Write 0.04 | 2.7% | |
| conversation: user 0.7 · assistant text 0.2 | 0.9% | headless agents barely narrate |
| polling turns (`sleep` in Bash) | 0.5% | 6.1% in my own interactive session |

Two consequences this forces:

- **B3 has essentially maxed out the tool-result slice.** Its 8.3% safe NET is ~70% of a 12% slice.
  Anything else aimed at tool outputs — ReadIfChanged, canonical-object dedup, bash trimming — is
  fighting over the remaining ~3–4 points. Measured: same-path refresh reads are **2.1k tokens per
  session** (~1% of Σ P_t); edit `old_string` duplicating a resident read: **0**. `ReadIfChanged` is a
  ~1% lever on this workload, not a priority.
- **73% of the workload is the prefix nobody has touched**, re-read on every one of ~50 calls.

**Cost vs tokens** (same sessions, 1h-cache pricing: read 0.1×, write 2×, output 5×):
cache-read is **97.6% of tokens and 60.8% of cost**; cache-write 1.8% / **22.4%**; output (incl.
thinking) 0.5% / **16.7%**. On 50-call sessions both denominators are dominated by the re-read prefix;
on short sessions (the 7-call proxy run) cache *writes* dominate cost (83%). A "50% cost" target adds
"what enters" and "output" levers on top of the prefix ones.

## 2. The turn profile — the multiplier

Over 2,968 real API calls: **discovery (read/grep/ls) = 51% of calls**, other-bash 19%, test/exec 16%,
edit 12%, final answer 2%. Discovery comes in **runs of 3.5 consecutive calls** (max 32). If every
consecutive-discovery run were collapsed into ONE local call, **37.5% of all API calls disappear** — an
upper bound, but each avoided call skips a full re-read of the ~73% fixed prefix, so call reduction
maps almost 1:1 onto Σ P_t reduction. Run-to-run variance (same task, 5 reps) is 1.9× max/min; tokens
beyond 1.5× the task median are only 4.6% — a runaway-governor is a small lever here.

## 3. What the outside evidence adds (and what it doesn't)

- **Tool-definition overhead is real and large.** Anthropic: a traditional multi-MCP setup spends
  ~77k tokens on tool definitions before work begins; Tool Search (`defer_loading`) cuts it to ~8.7k
  (−85%) *and* raises accuracy (Opus 4: 49→74%). Third-party measurements put real setups at 55–134k.
  Our proxy run saw **82k of startup prefix** on this machine vs 42k in the lean django env — the 40k
  difference is entirely MCP servers / plugins / skills. Claude Code already defers some tools; the
  subscription client does not yet expose Tool Search (issue #12836).
- **Context editing is native now**: `clear_tool_uses_20250919` (trigger default 100k input tokens,
  `keep` 3, `clear_at_least`, `exclude_tools`, **`clear_tool_inputs`**), `clear_thinking_20251015`, and
  server-side compaction. Clearing invalidates the cache at the edit point — exactly B3.2's rewrite
  economics — and the API reports `cleared_input_tokens`. It is an API/gateway feature, not a
  subscription-client one. Note `clear_tool_inputs`: Anthropic also retires tool *inputs* (our
  `tool_use_edit/bash` 2.2%).
- **Programmatic tool calling**: −37% tokens (43.6k→27.3k) on research tasks; explicitly *not* helpful
  for sequential single calls. Our 37.5% collapsible-call bound is the coding-workload analogue — the
  number to validate.
- **Token-consumption studies** agree with our shape: input dominates (1000× chat), read-type ops are
  ~76% of tokens (SWE-Pruner), redundancy grows with trajectory length, and accuracy *peaks at
  intermediate cost* — spending more tokens does not buy correctness. One striking line: Sonnet 4.5
  and Kimi consume ~1.5M more tokens than GPT-5 on identical tasks — trajectory *style* is itself a
  first-order lever.
- **Thinking retention**: Opus 4.5+ / Sonnet 4.6+ keep ALL prior thinking blocks in context by default
  (earlier models kept only the last turn). That is our invisible 11.3%, and it is largest in
  interactive Opus sessions.

## 4. How I (the agent) waste tokens — a self-audit on my own 1,236-call session

Measured on this workstream's transcript: inline heredoc scripts carried forever in `tool_use` inputs
(**71k tokens**), same-path refresh re-reads (**29.6k**), **61 polling turns (6.1% of Σ P_t)**, Write
contents (12% of visible), Edit inputs (13.6%), and thinking as the largest visible component. Patterns,
each fixable by behaviour or by the runtime:

1. **Polling with `sleep` loops** — every poll is a full API call re-reading the whole prefix. A single
   long-timeout wait or a Monitor tool costs one call.
2. **Inline scripts in Bash** — a 2k-token heredoc is resident for the rest of the session; writing it
   to a file once and running it keeps only a path in context.
3. **Read-before-Edit re-reads** — the client enforces "Read before Edit"; after retirement or
   compaction that forces a re-read. `ReadIfChanged` helps here (small), but the cleaner fix is letting
   the edit precondition be satisfied by a content hash.
4. **Full-file reads when a slice would do**; `cat` via Bash (no line numbers, no budget) instead of a
   bounded Read.
5. **Narrating between calls, long commit/PR bodies** — output at 5× that then becomes input forever.
6. **Retained thinking** — not a choice I make per call, but the biggest invisible cost in interactive
   Opus sessions; it is exactly what `clear_thinking` exists for.

## 5. The lever stack, sized in OUR denominator

Estimates are share of Σ P_t on the django-like workload unless noted; "subscription" = the Claude Code
subscription client, "gateway" = our proxy / API path where we own the request.

| # | lever | est. Σ P_t | where | confidence | status |
|---|---|---:|---|---|---|
| 1 | **Fixed-prefix hygiene**: measure startup prefix, itemize MCP/plugins/skills/CLAUDE.md/memory, defer or drop unused (Tool Search pattern) | **−10 to −40** (lean env 42k → mostly Claude Code's own prompt, limited; heavy env 82k → halving it alone ≈ −40) | subscription (config) + gateway | high — it's arithmetic on the 73% | **not built** — `cr doctor --prefix` |
| 2 | **Collapse discovery runs** into one local call (evidence packet / PTC-style executor) | **−10 to −25** (bound −37.5% of calls) | gateway / custom loop; changes the tool surface | medium — biggest upside, adoption risk (cf. 0/11 SemanticFS) | **not built** — measure first |
| 3 | **B3 context retirement** | **−8** (safe NET) | gateway (done, OBSERVE) | high, live-sanity-checked | **built** |
| 4 | **Thinking GC** (keep last N thinking turns — B3 for thinking; `clear_thinking` natively) | **−5 to −10** (11.3% slice; more in interactive Opus) | gateway / API | high — Anthropic ships it; old models did it by default | **not built** — small |
| 5 | Cache geometry (stable prefix ordering, no churn in tool lists, volatile data last) | 0 tokens, **cost** only (protects the 0.1× read) | gateway | high | partly inherent |
| 6 | Output discipline (thinking budget, terse narration, scripts-to-files, no polling) | 0.5–6 (polling) + cost (17% output slice) | agent behaviour / prompts | high, cheap | guidance |
| 7 | ReadIfChanged / canonical-object dedup / bash trimming | **−1 to −3** combined | gateway | high, small | deprioritize |
| 8 | Runaway-trajectory governor | −2 to −5 | any | medium | deprioritize |

**Arithmetic for 50% (corrected: multiplicative, not additive).** Levers overlap multiplicatively —
each acts on the workload the previous ones left. E.g. prefix −20%, calls −15%, B3 −8%, thinking −7%
compose to 0.80×0.85×0.92×0.93 ≈ 0.582, i.e. **−41.8%**, not −50%. Reaching 50% needs roughly prefix
−30% × calls −15% × B3 × thinking (≈ −49%) or a stronger stack. The measured prefix numbers are now in
`docs/prefix-doctor-findings.md`: subscription-achievable −27.6% (heavy) / −31.9% (lean) of Σ P_t.

**Accounting correction (measured while reconciling the doctor):** the **Claude request-accounting
factor is ~1.74× the cl100k estimate** on coding-agent content (455 clean call-deltas, IQR 1.61–1.89).
This is an accounting ratio, not a tokenizer-vocabulary claim — it may include serialization/protocol/
invisible content; only the official count-tokens endpoint on identical bytes would separate those.
Either way, heuristic/tiktoken estimates understate Claude-billed usage by ~40% absolute;
ratios/percentages are unaffected.

**Arithmetic for 50% (original, superseded by the above).** On the lean workload, B3 (8) + thinking GC (8) + ReadIfChanged-class (2) =
~18% — *the ceiling of everything that operates on content*. The rest must come from the two
multipliers: the fixed prefix (#1) and the call count (#2). 50% is reachable only as
**#1 + #2 + #3 + #4 together**, e.g. −20 (prefix) −15 (calls) −8 (B3) −7 (thinking) ≈ −50. In heavy-MCP
environments #1 alone can clear 40%. On the subscription client, #1 is the user's configuration and #2 is
not available — so **50% on the subscription client is not credible without Anthropic-side features
(Tool Search, context editing) arriving in Claude Code**; on the gateway/custom-loop path it is.

This is the sharpened version of the "control what enters and how long it stays" thesis: the *what
enters* that matters most is **the prefix and the number of times it is re-read**, not the retrieval
cleverness of individual tool results. Localization / canonical objects / graph-as-ranking-signal are
good engineering for *quality per admitted token*, but in this denominator they are small-single-digit.

## 6. Recommended next builds, in order (all measurable before any quota)

1. **`cr doctor --prefix`** — read the first call's `cache_creation` from the session (or the OBSERVE
   log), report the startup prefix, and itemize the controllable parts with token estimates (each MCP
   server's tool schemas, each skill description, CLAUDE.md, memory). Zero risk, immediate, and in heavy
   environments it is the single largest saving available anywhere in this program.
2. **Thinking GC in the gateway** — `RetirementPlanner` already has the shape; add `thinking` blocks as
   retirable objects with a keep-last-N policy, OBSERVE-first. ~20 lines on top of B4.
3. **Discovery-collapse measurement** — from the OBSERVE log, log discovery runs and what each run's
   calls fetched; that gives the realistic (not upper-bound) collapsible share and the evidence-packet
   size needed. Only then design the executor/tool surface, and only then spend quota.
4. Keep B3 in OBSERVE on real traffic (running) → ENFORCE superseded-only.

Frozen B1, B2 artifacts and the G1/G2 closure untouched; B3 docs carry a correction note for the
turn-axis restatement.

## Sources

- Anthropic, [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing) — `clear_tool_uses_20250919`, `clear_thinking_20251015`, cache interaction, `cleared_input_tokens`.
- Anthropic, [Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) — Tool Search (77k→8.7k, −85%; Opus 4 49→74%), Programmatic Tool Calling (−37%, 43,588→27,297; not for sequential calls).
- Anthropic, [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 5-min 1.25× / 1-hour 2× writes, 0.1× reads.
- Claude Code, [Support Tool Search and Programmatic Tool Use betas — issue #12836](https://github.com/anthropics/claude-code/issues/12836).
- Token Optimize, [Cut MCP and tool overhead](https://www.tokenoptimize.dev/guides/reduce-tool-overhead-mcp-tokens) — 55k–134k tool-definition overhead in real setups.
- [How Do AI Agents Spend Your Money? (arXiv 2604.22750)](https://arxiv.org/abs/2604.22750) — 1000× chat, input-dominated, 30× run variance, accuracy peaks at intermediate cost.
- [SWE-Pruner (arXiv 2601.16746)](https://arxiv.org/pdf/2601.16746) — read ops ~76% of tokens; redundancy grows with trajectory length; pruning does not degrade.
- [Context Editing … a Garbage Collector Without Write Barriers](https://conikeec.substack.com/p/context-editing-looks-like-a-feature); [Claude Cookbook: context engineering](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools).
