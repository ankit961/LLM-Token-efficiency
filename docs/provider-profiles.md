# The generic framework — provider profiles

**The program's goal was a ~50% reduction in what agentic coding spends on tokens.** The road to
it settled a thesis (`token efficiency ≈ admission control + lifetime control`) and produced a
runtime validated live on one provider (Anthropic, −41.5% workload / −29.3% dollars). This
document records the step that makes the framework **generic**: every algorithm in the runtime
reduces to four provider constants, so porting it is a calibration exercise, not a redesign.

## The abstraction

`contextruntime/providers.py` — a `ProviderProfile` = `{read_mult, write_mult, ttl_s, out_mult}`,
selected at the gateway by `CR_GATEWAY_PROFILE` (default: the validated `anthropic-1h`; unknown
names fall back to it — the strictest break-even, i.e. the safe direction). One derived number
governs the entire economics of history mutation:

    break_even_reads = (write_mult − read_mult) / read_mult
    = future cached reads one rewritten token must earn back before mid-session mutation pays

| profile | break-even | status |
|---|---:|---|
| `anthropic-1h` | **19** | **live-validated** (calibrated exact; 0.2pp on a preregistered live prediction — B7/B8) |
| `anthropic-5m` | 11.5 | same semantics, unvalidated constants |
| `openai-auto` | **1** | modeling preset — cached input ~50% off, **cache writes free** |
| `gemini-implicit` | 3 | modeling preset — ~75% hit discount, no write premium (explicit cache is storage-priced: an even more retirement-favorable objective this model doesn't represent) |

The scheduler inequality, the cache simulator (TTL, extent semantics), and the pricing all read
these constants; nothing else in the planner/mutator/gateway is provider-specific. Two levers are
Anthropic-ecosystem-specific and simply absent elsewhere: thinking-GC (other clients don't resend
reasoning) and the schema-deferral interaction (a Claude Code client behavior).

## Sensitivity: same sessions, same mutations, different constants — different verdicts

Zero-quota replay (`python -m corpus.b7_cache_replay providers [session-list]`) over the **same
real sessions and identical mutation streams**, rescheduled and repriced per profile:

**12 short headless sessions (B6 natives):**

| profile | unaligned Δ$ | gated Δ$ | gated fires |
|---|---:|---:|---:|
| anthropic-1h | +9.0% | **0.0%** (holds — correct) | 1 |
| anthropic-5m | +3.4% | −0.2% | 3 |
| openai-auto | −7.6% | **−9.6%** | 161 |
| gemini-implicit | −4.6% | −5.6% | 62 |

**54 real interactive sessions:**

| profile | unaligned Δ$ | gated Δ$ | gated Δresidency | gated fires |
|---|---:|---:|---:|---:|
| anthropic-1h | −51.4% | **−49.6%** | −52.3% | 3,398 |
| anthropic-5m | −58.1% | −59.1% | −61.0% | 4,447 |
| openai-auto | −63.1% | **−63.2%** | −64.9% | 8,829 |
| gemini-implicit | −61.8% | −62.0% | −64.0% | 6,597 |

The point of the tables: **one inequality, opposite live behavior from the constants alone.** At
break-even 19 the scheduler correctly refuses to mutate short sessions (the live-demonstrated
no-harm result); at break-even 1 the same code fires two orders of magnitude more often and
mutation pays even there. Free-write providers are strictly *more* favorable to this framework
than the one it was validated on.

## What porting to a new provider/model actually requires

1. **Verify the ratios** against current pricing (profiles ship as presets, not claims — same
   rule as `pricing.json`).
2. **Calibrate the cache semantics** on real transcripts (`calibrate_append_only` + the edit
   branch): Anthropic's partial interior hits and soft TTL had to be *discovered*; other
   providers will have their own surprises, and the model is only trustworthy where it
   reproduces observed billing.
3. **Re-run the behavioral safety check** (a ~$5 B3.3-style live A/B): that a model tolerates
   retirement stubs without re-read spirals is a per-model property, measured so far on sonnet
   only.
4. Then the standard ladder: offline replay → preregistered band → small live A/B (the B7→B8
   playbook, which landed within 0.2pp on its first provider).

Until steps 1–3 are done for a given provider, its column above is a *model of a model* — useful
for prioritization (OpenAI is the strongest a-priori case for the retirement lever), quotable
only as such.
