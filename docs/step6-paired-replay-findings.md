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
  (p50 = 90), the shipped floor is far too high.

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

**Recall is 100% at every config, including the aggressive `(64,125)`.** Even reducing the small
greps, the compact output still names every file the agent went on to touch (the per-file rollup
carries them), and the recovery handle backstops the rest. Lowering the floor 4× loses no evidence
in this data.

## 3. The graph question — answered

Because **simple reduction already achieves 100% path-recall**, there is **no room for graph
ranking to improve retention quality** here — its entire value proposition (keep the *right*
matches) is already satisfied by the count-ranked rollup + budget on this workload. On this data
**graph ranking is not justified**: it adds machinery (a per-task code graph, working-set state)
for a retention metric that is already maxed out. (The graph arm would only earn its keep on a
dataset where simple reduction *drops* subsequently-needed paths — not observed here.)

## Verdict

- **Reduction is real and measured, not estimated:** shipped `R_paired = 12.1%` on live outputs.
- **The tuning lever is the FLOOR:** `floor ≈ 125` roughly **quadruples** capture (12% → 54%) and,
  in this data, is **lossless for future-needed paths** (100% recall). Recommend evaluating a lower
  default floor.
- **Graph ranking is not justified on this workload** — simple is already lossless.

## Caveats

Sample is modest (16 sonnet sessions, 184 events, 2–20 recall-scored reductions covering 5–31
subsequently-needed paths depending on config), django-only, one model. The direction is clear and consistent, but the lower-floor default and the drop-graph
call deserve confirmation on a broader task/model mix before shipping. This is evidence to DECIDE
the next default and to deprioritize graph — not yet a final production setting.
