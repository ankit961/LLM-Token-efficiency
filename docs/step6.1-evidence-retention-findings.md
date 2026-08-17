# Step 6.1 — line-level evidence-retention replay (findings)

**Run 2026-08-18, ZERO Claude quota.** A reviewer correctly flagged that Step 6's headline —
"100% path-recall ⇒ no room for graph ranking to improve" — does **not** follow from the metric.
`path_recall` asks only whether a subsequently-touched file's path *string* still appears in the
compact output. But when the reducer truncates it emits a per-file rollup that **names up to 12
matched files even when their actual match lines were dropped**. So `named(path) ≠ useful line
retained`, and graph ranking changes exactly the thing name-recall is blind to: *which match lines*
survive the budget. Step 6.1 measures the right thing.

## Method

Same 16 pilot transcripts and same paired discipline as Step 6, but per reducible-and-firing search
event we now run **both** reducers on the identical raw output:

- **graph arm** uses a REAL per-task django code graph (the pilot's own `C_graph/codegraph.db`,
  indexed at that task's `base_commit`, 2.6–2.8k files, ~145k working-set edges) and a working set
  built **only from events preceding the search** (`touched` = files Read/Edit/Written earlier in
  the trajectory) — exactly what the live hook would have had.
- For each subsequently-needed path we record **Named** (path string present) vs **LineRetained**
  (≥1 real `path:lineno:` match line survived, not merely the rollup name), under each arm.

Faithfulness: reuses the reducer's own constants, `graphrank.path_scores` collapse,
`_kept_match_lines`, and the real `reduce_search`. Only `_proximity` is reimplemented in-memory
(django's degree-6857 hub makes the sqlite-per-node walk too slow to sweep); it is unit-tested to
match `graphrank._proximity` byte-for-byte on a fake store over identical edges
(`tests/test_graph_evidence_replay.py`). Reproduce:
`python -m corpus.graph_evidence_replay '<transcript-glob>' <pilot_dir>`.

## Results

| config (b,f) | needed | graph active events | kept match-lines S/G | graph reordered | **name-recall** | **LINE-recall S/G** | expansion pressure | promoted-needed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| (256,**400**) shipped | 5 | 0 | 7/7 | 0 | 1.00 | **0.40** | 3/5 | 0 |
| (256,244) | 16 | 7 | 74/73 | 1 | 1.00 | **0.56** | 7/16 | 0 |
| (256,125) | 16 | 7 | 74/73 | 1 | 1.00 | **0.56** | 7/16 | 0 |
| (64,244) | 16 | 7 | 2/2 | 0 | 1.00 | **0.125** | 14/16 | 0 |
| (64,**125**) aggressive | 31 | 12 | 3/3 | 0 | 1.00 | **0.097** | 28/31 | 0 |

## 1. "Lossless" was wrong — name-recall hid a large line-level loss

Name-recall is 100% everywhere (Step 6's number), but **LINE-recall is 0.40 at the shipped setting
and falls to ~0.10 at the aggressive floor**. At `(64,125)`, **28 of 31** subsequently-needed paths
survive as a rollup *name only* — their actual match line is gone and would require a `result://`
expansion to recover. The compression is real, but it is achieved largely by **replacing match
lines with file names + a recovery handle**, not by keeping the useful lines. The honest statement
is therefore *"names retained, most needed match lines moved behind an exact `result://` handle"* —
**not** "lossless".

## 2. Budget — not floor — is the line-retention lever

Kept match-lines collapse from **74** at budget 256 to **2–3** at budget 64. So the very move that
buys the big token capture (dropping budget 256→64 to push `R_paired` from ~18% to ~54%) is what
strips inline evidence: line-recall 0.56 → 0.10. The floor decides *how often* reduction fires; the
**budget decides how much real evidence survives inside each reduced result**. This reframes Step
6's "lower the floor to 125 for 54%" as a genuine trade: more token capture ⇒ fewer inline lines ⇒
more reliance on `result://` expansion.

## 3. Graph ranking — no detectable line-level advantage (and NOT disproven)

Correcting Step 6's inference with a direct test:

- Where the test has **power** — budget 256, graph active on 7 events, 74 lines available to
  reorder — graph moved exactly **1 line out of 74**, rescued **0** subsequently-needed lines,
  produced **identical** line-recall (0.56 vs 0.56), and a mean rank-gain of 0.22 positions on
  lines both arms already kept.
- Where graph is **underpowered** — budget 64 — only 2–3 lines survive under *either* ordering, so
  there is nothing to reorder (`graph_reordered_any = 0`); this is a budget artifact, not evidence.
- On the **shipped** `(256,400)` config graph never engaged (0 active events) — consistent with the
  live pilot's `graph_ranked = 0`.

Across **every** config, graph rescued **zero** needed lines (`promoted_needed = 0`). So the honest
verdict is **"no detectable graph advantage at the evidence-line level"** — a real (small-sample)
negative where testable, and *untestable* where the budget leaves no lines to rank. This is **not**
"graph disproven". What is now solid: paired replay, even at the line level, gives graph ranking no
demonstrated value on the B1 search-reduction path, so it should stay **deprioritized**, not on the
critical path.

## Revised verdict (supersedes Step 6 §2–§3)

- **Token compression is real and measured:** `R_paired(256,400)=12.1%`, `(64,125)=53.9%` (Step 6).
- **Not "lossless".** Aggressive settings keep the file *names* but drop ~90% of subsequently-needed
  match *lines* to the `result://` handle. The floor/budget choice trades token-capture against
  inline-evidence, and the right operating point depends on the **live cost of `result://`
  expansions** (extra turns, cache re-reads, retries) — which only the deferred replicated live
  experiment can price (measure `T_total`, turns-to-resolution, retry burden, task success).
- **Graph ranking:** no line-level benefit where the test has power; untestable at low budget;
  `promoted_needed = 0` throughout. Deprioritize on the evidence; do not claim it disproven.

## Caveats

16 sonnet sessions, django only, 5–31 needed paths per config; the graph arm is genuinely powered
only at budget 256 (7 active events, one reordering observed). A broader task/model mix, and a live
measurement of expansion cost, remain necessary before fixing a default budget/floor or removing
graph from the codebase.
