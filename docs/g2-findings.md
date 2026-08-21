# G2 — Task-Relevance Graph ceiling: offline findings + skeptical review

**Run 2026-08-22, ZERO Claude quota. No live experiment.** Offline replay over the same 4 native
(A_native) Step-7 django trajectories + per-task code graphs used by G1. Harness
`corpus/g2_replay.py`; per-layer rows, ceilings, and provenance in `corpus/analysis/g2-results.json`.

## The question G2 answers (the *right* value function)

G1's fair ablation showed the STRUCTURAL dependency graph (`CALLS/IMPORTS/IMPLEMENTS`) rarely links
co-*edited* symbols — but co-edit prediction is the wrong target. The right one: **given a correct
anchor (the edited symbols — so anchor resolution, which G1 showed also fails, is held constant at its
best case), does a cheap deterministic graph reach the SUPPORT symbols the agent actually READ but
never edited — the surrounding context it needed to make the fix — inside a real model-visible token
budget?**

- **Ground truth** = symbols the agent inspected (Read) over the trajectory, MINUS the edited ones.
- **Layers** (relations available BEFORE inference; future edits are ground truth only, never a
  feature): `lexical` = target symbols only · `Gstruct` = CALLS+IMPORTS+IMPLEMENTS ·
  `Glocal` = Gstruct + same-file + same-class + identifier-refs in the anchor body ·
  `Gtask` = Glocal + tests + lexical source-search + **historical git co-change**.
- **Co-change** is computed STRICTLY from commits that are ancestors of each task's base commit
  (`git log <base_commit>`; the fix commit is a descendant) — **no future-edit leakage**.
- **Two measures**, deliberately separated:
  - **Ceiling** — fraction of needed support the layer reaches *ignoring the budget*. Answers *is the
    relation informative?*
  - **Budget recall** — fraction reachable *inside* a real token budget (targets rendered at
    implementation = must-keep; support at signature by ascending rank until the budget is spent).
    Local traversal/search tokens are NOT counted — only the compiled bundle's tokens. Answers *can a
    budget realize the ceiling?*

Corpus: 3 scorable tasks (10554: 89 support / 4 edited; 11138: 72 / 6; 14608: 17 / 1). 12419 has zero
edited symbols → no oracle anchor → excluded.

## Preregistered HARD gate

> `R_support(Gtask) − R_support(lexical) ≥ 0.15` **at comparable-or-lower model tokens** ⇒ the graph
> is a real token-saving mechanism. Otherwise **close graph traversal as a primary token-saving
> mechanism.**

## Results

### Ceiling (budget-free reachability of needed support) — the relations ARE informative

| layer | ceiling (mean of 3 tasks) |
|---|---:|
| lexical (target only) | 0.00 |
| Gstruct (dependency edges) | **0.091** |
| Glocal (+ locality) | **0.616** |
| Gtask (+ tests + lexical + co-change) | **0.739** |
| — co-change's marginal lift (Gtask − Glocal ceiling) | **+0.123** |

Per task, Gtask ceiling = 0.944 / 0.625 / 0.647. So a task-relevance graph **can** reach ~74% of the
support the agent read — and **co-change genuinely helps** (10554: 0.80→0.94; 11138: 0.40→0.63). This
is not "graphs are useless."

### Budget-constrained recall — but a budget can't realize the ceiling

| budget | lexical | Gstruct | Glocal | Gtask |
|---|---:|---:|---:|---:|
| @1024 | 0.00 (1391t) | 0.078 (1433t) | 0.137 (1529t) | 0.137 (1529t) |
| @2048 | 0.00 (1391t) | 0.088 (1471t) | 0.209 (2070t) | 0.209 (2071t) |
| @4096 | 0.00 (1391t) | 0.091 (1510t) | 0.293 (3612t) | **0.226** (4096t) |

Two facts collapse the ceiling:

1. **Gtask ≈ Glocal, and at @4096 Gtask < Glocal (0.226 < 0.293).** Co-change and tests operate at
   FILE granularity — they surface a whole co-changed file's symbols at once. Under a budget those
   flood the candidate pool and *crowd out* the same-file support that locality already found. The
   graph knows the right *file*; it cannot say which *symbol* in it you need.
2. **Locality does all the within-budget work.** The only lift over lexical that survives a budget is
   `Glocal` (same-file / same-class / identifier) — which is essentially "include the relevant file,"
   not a graph traversal.

### Against the gate — FAIL at every budget

| budget | Δrecall (Gtask − lexical) | tokens (Gtask vs lexical) | verdict |
|---|---:|---|---|
| @1024 | +0.137 (< 0.15) | 1529 vs 1391 (comparable) | **FAIL** (recall) |
| @2048 | +0.209 (≥ 0.15) | 2071 vs 1391 (**+49%**) | **FAIL** (tokens) |
| @4096 | +0.226 (≥ 0.15) | 4096 vs 1391 (**+195%**) | **FAIL** (tokens) |

No budget clears **both** conditions: where the recall gap reaches 0.15 it is bought with 49–195%
more tokens, never at comparable cost.

### The decisive comparison — dominated by native reading

Native reading already achieves **1.0 support recall at ~2046 tokens** (from G1: the agent read the
support, that IS its native exploration cost). Gtask reaches **0.226 support recall at 4096 tokens** —
**twice the tokens for under a quarter of the support.** On this corpus the graph bundle is strictly
dominated: you would still have to natively read the missing ~75%, at no net saving.

## Verdict

**CLOSE graph traversal as a primary token-saving mechanism** — the preregistered gate fails at every
budget, and the bundle is strictly dominated by native reading.

The honest reason is sharper than "graphs don't work":

1. **The relations are informative** — Gtask's ceiling is ~0.74, and co-change (computed leak-free
   from pre-base history) adds a real +0.12. The task-relevance graph reaches the right *files*.
2. **But deterministic file/structural relations lack symbol-level PRECISION.** They cannot pick the
   needed symbol inside a relevant file, so realizing the ceiling means including whole files — which
   is native reading, at no saving. The within-budget lift that remains (locality) ≈ "read the file."
3. **So the bottleneck is retrieval precision within relevant files, not graph connectivity.** That
   is a ranking/embedding problem, not a graph-traversal one — and it is not the token-saving lever
   this program set out to validate.

Combined with G1 (compiler is sound given an anchor; anchor-from-prose fails; dependency edges miss
co-edits), the graph-first path is now thoroughly characterized offline and does **not** clear the bar
for a live experiment.

## Skeptical review (what keeps this honest)

- **n = 3 tasks.** Suggestive, not decisive. Report the numbers descriptively; do not treat 0.74 or
  0.23 as corpus-level constants.
- **Inflated support denominator.** "Needed support" = every symbol overlapping a Read line-range,
  including code the agent read but did not truly need (whole-file reads inflate it). This *understates*
  absolute recall — but the direction of the result (ceiling high, budget-recall low, native reading
  dominates) is robust to it, and the *dominance* margin (0.23 @ 4096t vs 1.0 @ ~2046t) is stark.
- **Oracle anchors = the graph's BEST case.** We hand the graph the edited symbols. Real pipelines
  must first resolve the anchor from prose, which G1 showed fails (0/14). So budget-recall here is an
  upper bound on an end-to-end system.
- **Co-change is file-level.** A symbol-level co-change signal (map each historical hunk to the symbol
  it touched) *might* raise budget-recall by ranking within a co-changed file — but that requires
  per-hunk→symbol attribution across history and is speculative. It is the one refinement this run
  does not test; everything cheaper than it fails.
- **Local traversal/search tokens are free here** (they stay outside model context). That is generous
  to the graph, and it still fails.

The 0/11 advisory-adoption result stands for what it measured; this does not overturn it. Frozen B1
and the B2 evidence artifacts were not touched. **No live experiment is warranted.**
