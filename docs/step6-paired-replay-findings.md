# Step 6 — paired counterfactual replay (findings)

**Run 2026-08-17, ZERO Claude quota.** The Step-5 pilot's whole-session Δtokens was swamped by
independent-trajectory variance and its graph arm never fired. Step 6 removes both problems by
holding the trajectory **fixed**: it replays the EXACT tool outputs the agent received (from the 16
pilot session transcripts, `~/.claude/projects/**/*.jsonl`) through the reducer counterfactually.
Because the same 184 search events are scored under every config, the comparison is **paired**.
`corpus/paired_replay.py`; reuses `measured_reduction` (the real gate + reducer, floor +
non-beneficial + exact-recovery gates), so it mirrors the live hook exactly.

## Data

16 transcripts → **184 reducible search events** with real raw output; **27,308** raw search-output
tokens. Output sizes: **p50 = 93 tok**, max 1,489; only **9/184 (5%) ≥ 400**, 84/184 (46%) ≥ 125.
The bucket is small-read-dominated — exactly Step-4's warning, now confirmed on live outputs.

## 1. R_paired — reduction along the ACTUAL trajectory (measured, not estimated)

    R_paired(B,F) = 1 − Σ measured_reduction(x_i; B,F) / Σ T(x_i)   over the 184 observed events

| budget | floor | events fired | R_paired |
|---:|---:|---:|---:|
| 256 | **400** (shipped) | 7 | **0.121** |
| 64 | 400 | 7 | 0.155 |
| 128 | 244 | 31 | 0.298 |
| 64 | 244 | 31 | **0.344** |
| 128 | 125 | 78 | 0.427 |
| **64** | **125** | 78 | **0.539** |

- **The shipped `(256,400)` gives `R_paired = 12.1%` — a MEASURED number that lands exactly on
  Step-4's 12.1% metadata estimate.** The cap model was right.
- **The floor is the dominant lever, not the budget.** At floor 400 only 7/184 events clear it;
  lowering the floor to 125 fires 78 and takes `R_paired` to **53.9%**. Because outputs are small
  (p50 = 93), the shipped floor is far too high.

## 2. Path-recall — is aggressive reduction SAFE?

For each reduced event, `needed` = matched files the agent read/edited in the next 8 steps;
`path_recall` = fraction of `needed` still NAMED in the compact output (kept line or per-file
rollup), the rest recoverable via `result://`. Path matching is **full-path, component-aware**
(`_suffix_match`), NOT basename — so the absolute worktree touch-path lines up with the
repo-relative grep-path, and django's dozens of same-named files (`models.py`, `tests.py`) are
never conflated. (Basename matching *under*counts `needed` by merging distinct dirs; the honest
full-path counts below are slightly higher and still fully retained.)

| config | scored reductions | needed paths | retained | path_recall | misses |
|---|---:|---:|---:|---:|---:|
| (256, 400) | 2 | 5 | 5 | **1.00** | 0 |
| (64, 244) | 10 | 16 | 16 | **1.00** | 0 |
| (64, 125) | 20 | 31 | 31 | **1.00** | 0 |

**Path-NAME recall is 100% at every config, including the aggressive `(64,125)`.** Even reducing
the small greps, the compact output still *names* every file the agent went on to touch, and the
recovery handle backstops the rest.

Two honest limits on what this proves — it is **not** "lossless":

- **Named ≠ useful line retained.** `path_recall` asks only whether a subsequently-touched file's
  path *string* still appears in the compact output. But when the reducer truncates it emits a
  per-file rollup that *names* up to 12 matched files **even when their actual match lines were
  dropped**. So a path can score as "retained" while the specific useful line is gone (recoverable
  only by expanding `result://`). Whether the useful *line* survives is a stronger metric —
  measured in §3 (Step 6.1), not here.
- **The future-touch proxy misses direct use.** `needed` counts only files the agent later
  `Read`/`Edit`/`Write`. A grep line can be useful *in place* — the agent reads the matched line,
  infers the answer, and never opens the file — and such evidence is not scored as needed at all.

The defensible statement is therefore: **floor 125 achieved 100% observed future-path-NAME recall
on these 16 traces, with exact `result://` recovery available for any omitted content** — not
"floor 125 is lossless".

## 3. The graph question — SUPERSEDED by Step 6.1 (see docs/step6.1-evidence-retention-findings.md)

> **Correction.** An earlier version of this section argued: "simple reduction already achieves 100%
> path-recall ⇒ no room for graph ranking to improve." **That inference is wrong.** `path_recall`
> only asks whether a subsequently-touched file's *name* still appears — and the reducer's per-file
> rollup names up to 12 files even when their match lines were dropped. `named ≠ useful line
> retained`, and graph ranking changes exactly which *lines* survive. Name-recall is therefore
> blind to graph's value proposition and cannot settle the question either way.

The correct, direct test is **Step 6.1** (line-level evidence-retention replay: real per-task graph,
working set from preceding events, LINE-retained vs Named). Its finding: **no detectable graph
advantage at the evidence-line level** — where the test is powered (budget 256, graph active on 7
events, 74 lines to reorder) graph moved 1 line, rescued 0 needed lines, identical line-recall;
where budget is 64 the test is underpowered (2–3 lines survive, nothing to reorder); on the shipped
`(256,400)` graph never engaged. `promoted_needed = 0` in every config. Verdict: **deprioritize
graph on the evidence — NOT "graph disproven".**

Step 6.1 also overturns this doc's "lossless" wording: name-recall is 100% but **LINE-recall is only
0.40 (shipped) → ~0.10 (aggressive)** — the compact output keeps file names but moves most
subsequently-needed match *lines* behind the `result://` handle.

## Verdict

- **Reduction is real and measured, not estimated:** shipped `R_paired = 12.1%` on live outputs.
- **Two levers, not one.** The **floor** sets how *often* reduction fires (floor 400 → 7/184 events,
  floor 125 → 78/184, `R_paired` 12% → 54%). The **budget** sets how much real evidence survives
  *inside* each reduced result (Step 6.1: budget 256 keeps ~74 match lines / line-recall 0.56;
  budget 64 keeps 2–3 / line-recall ~0.10). Big token capture at `(64,125)` comes precisely from
  dropping inline lines, so it is **not "lossless"** — 100% file-*name* recall but most needed
  *lines* move behind the `result://` handle. Pick the operating point by the **live cost of
  `result://` expansions**, not by token-capture alone.
- **Graph ranking: no detectable line-level advantage (Step 6.1, now run).** Directly tested with a
  real per-task graph: `promoted_needed = 0` in every config; identical line-recall where powered.
  Deprioritize on the evidence — not "disproven". See docs/step6.1-evidence-retention-findings.md.

## Caveats

Sample is modest (16 sonnet sessions, 184 events, 2–20 recall-scored reductions covering 5–31
subsequently-needed paths depending on config), django-only, one model. The direction is clear and consistent, but the lower-floor default and the drop-graph
call deserve confirmation on a broader task/model mix before shipping. This is evidence to DECIDE
the next default and to deprioritize graph — not yet a final production setting.
